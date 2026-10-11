"""Team: who can log in (Phase C1). Admin-only management, plus the public invite-acceptance step.

An invite produces a one-time link that is either emailed to the person (Brevo, or ``data/outbox`` on a
local deployment) or shown once to the admin, who sends it however they like. Only a SHA-256 of the
token is stored; a link works once, expires after 72 hours, and a newer invite revokes older ones.
Every change is audited (never the token).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from casefile.api.deps import get_db, require_admin
from casefile.api.routers.auth import user_out
from casefile.audit.log import append as audit
from casefile.bootstrap import hash_password
from casefile.db.models import AppUser, Org, UserInvite, UserRole
from casefile.db.tenant import set_tenant, system_scope
from casefile.invites import email_in_other_org, issue_invite, token_hash
from casefile.mailer import MailError, get_mailer
from casefile.signup import invite_mail

router = APIRouter(tags=["team"])
MIN_PASSWORD = 12
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _open_invite(db: Session, user_id: uuid.UUID, now: datetime) -> UserInvite | None:
    return db.scalar(
        select(UserInvite)
        .where(
            UserInvite.user_id == user_id,
            UserInvite.used_at.is_(None),
            UserInvite.revoked_at.is_(None),
            UserInvite.expires_at > now,
        )
        .order_by(UserInvite.created_at.desc())
        .limit(1)
    )


def _view(db: Session, u: AppUser, now: datetime) -> dict[str, Any]:
    invite = _open_invite(db, u.id, now)
    status = "active" if u.is_active else "invited" if invite else "no_login"
    return {
        "id": str(u.id),
        "email": u.email,
        "name": u.name,
        "role": u.role.value,
        "status": status,
        "crm_owner_id": u.crm_owner_id,
        "slack_linked": bool(u.slack_user_id),
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        "invite_expires_at": invite.expires_at.isoformat() if invite else None,
    }


def _member(db: Session, admin: AppUser, user_id: uuid.UUID) -> AppUser:
    user = db.get(AppUser, user_id)
    if user is None or user.org_id != admin.org_id:
        raise HTTPException(404, "user not found")
    return user


@router.get("/team")
def team(db: Session = Depends(get_db), admin: AppUser = Depends(require_admin)) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    users = db.scalars(select(AppUser).where(AppUser.org_id == admin.org_id).order_by(AppUser.name)).all()
    return [_view(db, u, now) for u in users]


class NewMember(BaseModel):
    email: str
    name: str = ""
    role: Literal["owner", "leader", "admin"] = "owner"


@router.post("/team")
def add_member(
    body: NewMember, db: Session = Depends(get_db), admin: AppUser = Depends(require_admin)
) -> dict[str, Any]:
    email = body.email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(422, "that does not look like an email address")
    if db.scalar(select(AppUser).where(AppUser.org_id == admin.org_id, AppUser.email == email)):
        raise HTTPException(409, "someone with that email is already on the team")
    if email_in_other_org(db, email, admin.org_id):
        raise HTTPException(409, "that email already has a Casefile login with another organisation")
    user = AppUser(
        org_id=admin.org_id,
        email=email,
        name=body.name.strip()[:200],
        role=UserRole(body.role),
        is_active=False,
    )
    db.add(user)
    db.flush()
    audit(
        db,
        admin.org_id,
        "user_added",
        actor_type="user",
        actor_id=str(admin.id),
        entity_type="user",
        entity_id=str(user.id),
        details={"email": email, "role": body.role},
    )
    db.commit()
    return _view(db, user, datetime.now(UTC))


class InviteIn(BaseModel):
    send_email: bool = False


@router.post("/team/{user_id}/invite")
def invite(
    user_id: uuid.UUID,
    body: InviteIn | None = None,
    db: Session = Depends(get_db),
    admin: AppUser = Depends(require_admin),
) -> dict[str, Any]:
    user = _member(db, admin, user_id)
    if email_in_other_org(db, user.email, user.org_id):
        raise HTTPException(409, "that email already belongs to another Casefile organisation")
    link, expires = issue_invite(db, user, actor_type="user", actor_id=str(admin.id), created_by=admin.id)
    emailed = False
    if body and body.send_email:
        org = db.get(Org, admin.org_id)
        assert org is not None
        try:
            get_mailer().send(invite_mail(user, org, admin, link))
        except MailError as exc:
            db.rollback()
            raise HTTPException(503, f"could not send the invite email: {exc}") from exc
        emailed = True
    db.commit()
    # An emailed link travels only in the email; otherwise it is shown to the admin this once.
    return {
        "link": None if emailed else link,
        "expires_at": expires.isoformat(),
        "shown_once": not emailed,
        "emailed": emailed,
    }


class MemberChange(BaseModel):
    role: Literal["owner", "leader", "admin"] | None = None
    active: bool | None = None


@router.patch("/team/{user_id}")
def change_member(
    user_id: uuid.UUID,
    body: MemberChange,
    db: Session = Depends(get_db),
    admin: AppUser = Depends(require_admin),
) -> dict[str, Any]:
    user = _member(db, admin, user_id)
    if user.id == admin.id:
        raise HTTPException(422, "you cannot change your own role or switch off your own login")
    changes: dict[str, Any] = {}
    if body.role is not None and body.role != user.role.value:
        changes["role"] = [user.role.value, body.role]
        user.role = UserRole(body.role)
    if body.active is False and user.is_active:
        user.is_active = False  # their session stops working on the next request
        changes["active"] = [True, False]
    if body.active is False:
        db.execute(
            update(UserInvite)
            .where(
                UserInvite.user_id == user.id, UserInvite.used_at.is_(None), UserInvite.revoked_at.is_(None)
            )
            .values(revoked_at=datetime.now(UTC))
        )
    if body.active is True and not user.is_active:
        if not user.password_hash:
            raise HTTPException(422, "this person has never set a password: send them an invite instead")
        user.is_active = True
        changes["active"] = [False, True]
    if changes:
        audit(
            db,
            admin.org_id,
            "user_changed",
            actor_type="user",
            actor_id=str(admin.id),
            entity_type="user",
            entity_id=str(user.id),
            details=changes,
        )
    db.commit()
    return _view(db, user, datetime.now(UTC))


# ---------------------------------------------------------------- the invited person (no login yet)


def _valid_invite(db: Session, token: str) -> tuple[UserInvite, AppUser]:
    system_scope(db)  # the link says nothing about the organisation until the token is found
    inv = db.scalar(select(UserInvite).where(UserInvite.token_hash == token_hash(token)))
    system_scope(db, False)
    if inv is not None:
        set_tenant(db, inv.org_id)
    if inv is None:
        raise HTTPException(404, "this invite link is not valid")
    if inv.used_at or inv.revoked_at or inv.expires_at <= datetime.now(UTC):
        raise HTTPException(410, "this invite link has expired or was already used; ask for a new one")
    user = db.get(AppUser, inv.user_id)
    assert user is not None
    return inv, user


@router.get("/auth/invite/{token}")
def invite_details(token: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    _, user = _valid_invite(db, token)
    org = db.get(Org, user.org_id)
    return {
        "email": user.email,
        "name": user.name,
        "organisation": org.name if org else "",
        "reset": bool(user.password_hash),  # a password-reset link, not a first invitation
    }


class AcceptIn(BaseModel):
    password: str


@router.post("/auth/invite/{token}")
def accept_invite(
    token: str, body: AcceptIn, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    inv, user = _valid_invite(db, token)
    if len(body.password) < MIN_PASSWORD:
        raise HTTPException(422, f"choose a password of at least {MIN_PASSWORD} characters")
    if body.password.lower() in {user.email, user.email.split("@")[0]}:
        raise HTTPException(422, "the password must not be your email address")
    now = datetime.now(UTC)
    user.password_hash, user.is_active, user.last_login_at = hash_password(body.password), True, now
    inv.used_at = now
    audit(
        db,
        user.org_id,
        "invite_accepted",
        actor_type="user",
        actor_id=str(user.id),
        entity_type="user",
        entity_id=str(user.id),
        details={"email": user.email},
    )
    db.commit()
    request.session.clear()
    request.session["uid"] = str(user.id)
    request.session["org"] = str(user.org_id)
    return user_out(user, db).model_dump()  # same shape as /auth/me, including the organisation's mode
