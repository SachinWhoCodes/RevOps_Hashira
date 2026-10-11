"""Idempotent setup helpers: organisation, users, connections."""

from __future__ import annotations

from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.db.models import AppUser, Connection, ConnectionKind, Org, UserRole, Zone

_hasher = PasswordHasher()

DEFAULT_INTERVALS = {
    ConnectionKind.fixture: 15,
    ConnectionKind.hubspot: 15,
    ConnectionKind.gmail: 5,
    ConnectionKind.gcal: 15,
    ConnectionKind.usage_sql: 60,
    ConnectionKind.usage_csv: 60,
}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def ensure_org(session: Session, *, slug: str, name: str, timezone: str, internal_domains: list[str]) -> Org:
    org = session.scalar(select(Org).where(Org.slug == slug))
    if org is None:
        org = Org(slug=slug, name=name, timezone=timezone, internal_domains=internal_domains, settings={})
        session.add(org)
    else:
        org.name, org.timezone = name, timezone
        org.internal_domains = sorted(set(org.internal_domains) | set(internal_domains))
    session.flush()
    return org


def upsert_user(
    session: Session,
    org: Org,
    *,
    email: str,
    name: str = "",
    role: UserRole = UserRole.owner,
    password: str | None = None,
) -> AppUser:
    email = email.lower()
    user = session.scalar(select(AppUser).where(AppUser.org_id == org.id, AppUser.email == email))
    if user is None:
        user = AppUser(org_id=org.id, email=email, name=name, role=role)
        session.add(user)
    if name:
        user.name = name
    user.role = role
    if password:
        user.password_hash, user.is_active = hash_password(password), True
    session.flush()
    return user


def ensure_connection(
    session: Session,
    org: Org,
    kind: ConnectionKind,
    *,
    name: str = "default",
    config: dict[str, Any] | None = None,
    secret_ref: str | None = None,
    zone: Zone = Zone.read,
) -> Connection:
    conn = session.scalar(
        select(Connection).where(
            Connection.org_id == org.id,
            Connection.kind == kind,
            Connection.zone == zone,
            Connection.name == name,
        )
    )
    if conn is None:
        conn = Connection(
            org_id=org.id,
            kind=kind,
            zone=zone,
            name=name,
            config=config or {},
            secret_ref=secret_ref,
            interval_minutes=DEFAULT_INTERVALS.get(kind, 15),
            cursor={},
        )
        session.add(conn)
    else:
        conn.config = {**(conn.config or {}), **(config or {})}
        conn.secret_ref = secret_ref or conn.secret_ref
    session.flush()
    return conn
