from __future__ import annotations

import uuid
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from casefile.db.models import AppUser, Org, UserRole
from casefile.db.session import get_sessionmaker
from casefile.db.tenant import set_tenant, system_scope

CSRF_HEADER = "x-casefile-csrf"


def get_db() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


def current_user(request: Request, db: Session = Depends(get_db)) -> AppUser:
    raw = request.session.get("uid")
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in")
    org = request.session.get("org")
    if org:
        set_tenant(db, org)  # row-level security: this request sees only the signed-in organisation
        user = db.get(AppUser, uuid.UUID(raw))
    else:  # a session from before organisations were stored in the cookie: look the user up once
        system_scope(db)
        user = db.get(AppUser, uuid.UUID(raw))
        system_scope(db, False)
        if user is not None:
            set_tenant(db, user.org_id)
            request.session["org"] = str(user.org_id)
    if user is None or not user.is_active or str(user.org_id) != (org or str(user.org_id)):
        request.session.clear()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in")
    return user


def current_org(user: AppUser = Depends(current_user), db: Session = Depends(get_db)) -> Org:
    org = db.get(Org, user.org_id)
    assert org is not None
    return org


def require_admin(user: AppUser = Depends(current_user)) -> AppUser:
    if user.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return user


async def csrf_guard(request: Request) -> None:
    """State-changing requests must carry a custom header; browsers can't send it cross-site
    without a CORS preflight, which we never allow."""
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing CSRF header")
