"""Self-service registration with email confirmation, and password reset by email.

1. ``start_signup`` stores a *pending* sign-up (``signup`` table) and emails a one-time link (24 h).
   Nothing else exists yet, so an unconfirmed sign-up cannot squat an email address or an organisation.
   Registering the same email again supersedes the older link.
2. ``complete_signup`` (the person clicked the link and pressed "Confirm") creates the organisation in
   setup mode (no AI spend, no writes — casefile.onboarding) and its first admin, already signed in.

No answer reveals whether an email has an account: an existing account gets a "you already have an
account" email instead of a confirmation link, and "forgot password" always says "sent".
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.audit.log import append as audit
from casefile.bootstrap import hash_password
from casefile.config import get_settings
from casefile.db.models import AppUser, Org, Signup, UserInvite, UserRole
from casefile.db.tenant import set_tenant, system_scope
from casefile.invites import INVITE_TTL, issue_invite, token_hash
from casefile.mailer import Mail, Mailer

SIGNUP_TTL = timedelta(hours=24)
MIN_PASSWORD = 12
RESETS_PER_HOUR = 3
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Personal mailbox providers: their domain is not "the company's own staff", so it is not used as an
# internal domain (the admin sets the real one on the Setup page).
PUBLIC_MAIL = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "msn.com",
    "yahoo.com",
    "ymail.com",
    "icloud.com",
    "me.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
    "zoho.com",
    "yandex.com",
    "gmx.com",
    "mail.com",
    "rediffmail.com",
}


class SignupError(ValueError):
    """Shown to the person as is."""


def _base() -> str:
    return get_settings().app_base_url.rstrip("/")


def _ip_hash(ip: str | None) -> str | None:
    if not ip:
        return None
    secret = get_settings().session_secret.get_secret_value()
    return hashlib.sha256(f"{secret}:{ip}".encode()).hexdigest()


def password_problem(password: str, email: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"choose a password of at least {MIN_PASSWORD} characters"
    if password.lower() in {email.lower(), email.split("@")[0].lower()}:
        return "the password must not be your email address"
    return None


def _login_exists(session: Session, email: str) -> bool:
    system_scope(session)  # one cross-organisation read: does any organisation have this login?
    try:
        return bool(
            session.scalar(
                select(func.count())
                .select_from(AppUser)
                .where(AppUser.email == email, AppUser.password_hash.is_not(None))
            )
        )
    finally:
        system_scope(session, False)


def start_signup(
    session: Session,
    mailer: Mailer,
    *,
    company: str,
    name: str,
    email: str,
    password: str,
    timezone: str,
    ip: str | None,
) -> None:
    settings = get_settings()
    if not settings.signup_enabled:
        raise SignupError("registration is closed on this server")
    email, company, name = email.strip().lower(), company.strip(), name.strip()
    if not EMAIL.match(email) or len(email) > 320:
        raise SignupError("enter a valid work email address")
    if not 2 <= len(company) <= 200:
        raise SignupError("enter your company's name")
    if problem := password_problem(password, email):
        raise SignupError(problem)
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        timezone = "UTC"
    now = datetime.now(UTC)
    hour_ago = now - timedelta(hours=1)
    recent = session.scalar(
        select(func.count()).select_from(Signup).where(Signup.email == email, Signup.created_at >= hour_ago)
    )
    if (recent or 0) >= settings.signups_per_email_per_hour:
        raise SignupError("too many attempts for this email; try again in an hour")
    ip_hash = _ip_hash(ip)
    if ip_hash:
        from_ip = session.scalar(
            select(func.count())
            .select_from(Signup)
            .where(Signup.ip_hash == ip_hash, Signup.created_at >= hour_ago)
        )
        if (from_ip or 0) >= settings.signups_per_ip_per_hour:
            raise SignupError("too many registrations from this network; try again in an hour")
    # Older links for this email stop working.
    for old in session.scalars(
        select(Signup).where(
            Signup.email == email, Signup.verified_at.is_(None), Signup.superseded_at.is_(None)
        )
    ):
        old.superseded_at = now
    token = secrets.token_urlsafe(32)
    session.add(
        Signup(
            email=email,
            name=name[:200],
            company=company[:200],
            timezone=timezone,
            password_hash=hash_password(password),
            token_hash=token_hash(token),
            ip_hash=ip_hash,
            expires_at=now + SIGNUP_TTL,
        )
    )
    session.flush()
    if _login_exists(session, email):
        mailer.send(
            Mail(
                to=email,
                to_name=name,
                subject="You already have a Casefile account",
                text=(
                    f"Someone (hopefully you) tried to register {email} on Casefile.\n\n"
                    f"This email already has an account. Sign in: {_base()}/login\n"
                    f"Forgot your password? {_base()}/forgot\n\n"
                    "If this wasn't you, ignore this email. Nothing has changed."
                ),
            )
        )
        return
    mailer.send(
        Mail(
            to=email,
            to_name=name,
            subject=f"Confirm your email to create {company} on Casefile",
            text=(
                f"Hi {name or 'there'},\n\n"
                f"Confirm your email to finish creating {company} on Casefile:\n"
                f"{_base()}/verify/{token}\n\n"
                "The link works once and expires in 24 hours. If you didn't sign up, ignore this email: "
                "nothing is created until the link is used.\n\n— Casefile"
            ),
        )
    )


def pending(session: Session, token: str) -> Signup:
    row = session.scalar(select(Signup).where(Signup.token_hash == token_hash(token)))
    now = datetime.now(UTC)
    if row is None or row.superseded_at is not None:
        raise SignupError("this link is not valid (a newer one may have been sent)")
    if row.verified_at is not None:
        raise SignupError("this link was already used; sign in instead")
    if row.expires_at <= now:
        raise SignupError("this link has expired; register again to get a new one")
    return row


def _slug(session: Session, company: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")[:40] or "company"
    slug = base
    system_scope(session)
    try:
        while session.scalar(select(Org.id).where(Org.slug == slug)) is not None:
            slug = f"{base}-{secrets.token_hex(2)}"
    finally:
        system_scope(session, False)
    return slug


def complete_signup(session: Session, token: str) -> AppUser:
    row = pending(session, token)
    if _login_exists(session, row.email):
        raise SignupError("this email already has an account; sign in or reset your password")
    domain = row.email.split("@", 1)[1]
    org_id = uuid.uuid4()
    set_tenant(session, org_id)  # row-level security: the new organisation's rows are written as itself
    org = Org(
        id=org_id,
        slug=_slug(session, row.company),
        name=row.company,
        timezone=row.timezone,
        internal_domains=[] if domain in PUBLIC_MAIL else [domain],
        settings={"mode": "setup"},
    )
    session.add(org)
    session.flush()
    user = AppUser(
        org_id=org.id,
        email=row.email,
        name=row.name,
        role=UserRole.admin,
        password_hash=row.password_hash,
        is_active=True,
        last_login_at=datetime.now(UTC),
    )
    session.add(user)
    row.verified_at, row.org_id = datetime.now(UTC), org.id
    session.flush()
    audit(
        session,
        org.id,
        "org_registered",
        actor_type="user",
        actor_id=str(user.id),
        entity_type="org",
        entity_id=str(org.id),
        details={"email": row.email, "company": row.company, "email_confirmed": True},
    )
    return user


def forgot_password(session: Session, mailer: Mailer, email: str) -> None:
    """Email a reset link if this email has an active login. Says nothing either way."""
    email = email.strip().lower()
    system_scope(session)
    try:
        users = session.scalars(
            select(AppUser).where(
                AppUser.email == email, AppUser.is_active.is_(True), AppUser.password_hash.is_not(None)
            )
        ).all()
    finally:
        system_scope(session, False)
    if len(users) != 1:
        return
    user = users[0]
    set_tenant(session, user.org_id)
    hour_ago = datetime.now(UTC) - timedelta(hours=1)
    recent = session.scalar(
        select(func.count())
        .select_from(UserInvite)
        .where(UserInvite.user_id == user.id, UserInvite.created_at >= hour_ago)
    )
    if (recent or 0) >= RESETS_PER_HOUR:
        return
    link, _ = issue_invite(session, user, actor_type="user", actor_id=str(user.id), created_by=None)
    mailer.send(
        Mail(
            to=user.email,
            to_name=user.name,
            subject="Reset your Casefile password",
            text=(
                f"Reset your Casefile password:\n{link}\n\n"
                f"The link works once and expires in {int(INVITE_TTL.total_seconds() // 3600)} hours. "
                "If you didn't ask for this, ignore this email; your password is unchanged."
            ),
        )
    )


def invite_mail(user: AppUser, org: Org, inviter: AppUser, link: str) -> Mail:
    return Mail(
        to=user.email,
        to_name=user.name,
        subject=f"{inviter.name or inviter.email} invited you to {org.name} on Casefile",
        text=(
            f"Hi {user.name or 'there'},\n\n"
            f"{inviter.name or inviter.email} invited you to {org.name} on Casefile. Set a password:\n"
            f"{link}\n\nThe link works once and expires in {int(INVITE_TTL.total_seconds() // 3600)} hours."
        ),
    )
