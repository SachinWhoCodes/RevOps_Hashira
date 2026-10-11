"""One-time invite links (C1), shared by the Team page and the operator's ``casefile create-org``.

Only a SHA-256 of the token is stored; a link works once, expires after 72 hours, and a newer invite
revokes older ones. The audit entry never contains the token.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from casefile.audit.log import append as audit
from casefile.config import get_settings
from casefile.db.models import AppUser, UserInvite
from casefile.db.tenant import system_scope

INVITE_TTL = timedelta(hours=72)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_invite(
    session: Session, user: AppUser, *, actor_type: str, actor_id: str, created_by: uuid.UUID | None
) -> tuple[str, datetime]:
    """Revoke the user's open invites and create a new one. Returns (link, expires_at); show it once."""
    now = datetime.now(UTC)
    session.execute(
        update(UserInvite)
        .where(UserInvite.user_id == user.id, UserInvite.used_at.is_(None), UserInvite.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    token = secrets.token_urlsafe(32)
    session.add(
        UserInvite(
            org_id=user.org_id,
            user_id=user.id,
            token_hash=token_hash(token),
            expires_at=now + INVITE_TTL,
            created_by=created_by,
        )
    )
    audit(
        session,
        user.org_id,
        "user_invited",
        actor_type=actor_type,
        actor_id=actor_id,
        entity_type="user",
        entity_id=str(user.id),
        details={"email": user.email, "expires_in_hours": 72, "resets_password": bool(user.password_hash)},
    )
    return f"{get_settings().app_base_url.rstrip('/')}/invite/{token}", now + INVITE_TTL


def email_in_other_org(session: Session, email: str, org_id: uuid.UUID | None) -> bool:
    """Logins are found by email alone, so one email may belong to only one organisation. This is a
    deliberate cross-organisation read (row-level security bypass for one query)."""
    system_scope(session)
    try:
        rows = session.scalars(select(AppUser.org_id).where(AppUser.email == email.strip().lower())).all()
    finally:
        system_scope(session, False)
    return any(o != org_id for o in rows)
