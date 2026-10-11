from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.api.deps import current_user, get_db
from casefile.bootstrap import verify_password
from casefile.config import get_settings
from casefile.db.models import AppUser, Org
from casefile.db.tenant import set_tenant, system_scope
from casefile.mailer import Mailer, MailError, get_mailer, mail_mode
from casefile.onboarding import org_mode
from casefile.signup import SignupError, complete_signup, forgot_password, pending, start_signup

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    org_name: str
    org_mode: str  # setup | shadow | live (casefile.onboarding); every page shows it when not live


def user_out(user: AppUser, db: Session) -> UserOut:
    org = db.get(Org, user.org_id)
    assert org is not None
    return UserOut(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role.value,
        org_name=org.name,
        org_mode=org_mode(org),
    )


@router.post("/login")
async def login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> UserOut:
    system_scope(db)  # the one cross-organisation read: who owns this email (row-level security)
    users = db.scalars(
        select(AppUser).where(AppUser.email == body.email.strip().lower(), AppUser.password_hash.is_not(None))
    ).all()
    system_scope(db, False)
    # One email, one organisation (enforced when people are added); if it ever breaks, refuse rather
    # than guess which company to sign in to.
    user = users[0] if len(users) == 1 else None
    if user is None or not user.is_active or not verify_password(user.password_hash, body.password):
        await asyncio.sleep(0.5)  # slow down guessing
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong email or password")
    set_tenant(db, user.org_id)
    request.session.clear()
    request.session["uid"] = str(user.id)
    request.session["org"] = str(user.org_id)
    user.last_login_at = datetime.now(UTC)
    db.commit()
    return user_out(user, db)


@router.post("/logout")
def logout(request: Request) -> dict[str, bool]:
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(user: AppUser = Depends(current_user), db: Session = Depends(get_db)) -> UserOut:
    return user_out(user, db)


# ---------------------------------------------------------------- self-service registration


class RegisterIn(BaseModel):
    company: str
    name: str = ""
    email: str
    password: str
    timezone: str = "UTC"


class EmailIn(BaseModel):
    email: str


def _mail_or_503() -> Mailer:
    try:
        return get_mailer()
    except MailError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.get("/signup-config")
def signup_config() -> dict[str, Any]:
    """Public: whether registration is open and how email goes out ('outbox' = local development)."""
    return {"enabled": get_settings().signup_enabled, "mail": mail_mode()}


@router.post("/register")
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    mailer = _mail_or_503()
    try:
        start_signup(
            db,
            mailer,
            company=body.company,
            name=body.name,
            email=body.email,
            password=body.password,
            timezone=body.timezone,
            ip=request.client.host if request.client else None,
        )
    except SignupError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except MailError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"could not send the email: {exc}") from exc
    db.commit()
    return {"sent": True, "email": body.email.strip().lower(), "mail": mail_mode()}


@router.get("/verify/{token}")
def verify_details(token: str, db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        row = pending(db, token)
    except SignupError as exc:
        raise HTTPException(status.HTTP_410_GONE, str(exc)) from exc
    return {"email": row.email, "company": row.company, "name": row.name}


@router.post("/verify/{token}")
def verify(token: str, request: Request, db: Session = Depends(get_db)) -> UserOut:
    """Confirming is a button press (POST), so mail scanners that open links cannot create accounts."""
    try:
        user = complete_signup(db, token)
    except SignupError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_410_GONE, str(exc)) from exc
    db.commit()
    request.session.clear()
    request.session["uid"] = str(user.id)
    request.session["org"] = str(user.org_id)
    return user_out(user, db)


@router.post("/forgot")
def forgot(body: EmailIn, db: Session = Depends(get_db)) -> dict[str, bool]:
    mailer = _mail_or_503()
    try:
        forgot_password(db, mailer, body.email)
    except MailError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"could not send the email: {exc}") from exc
    db.commit()
    return {"sent": True}  # the same answer whether or not the email has an account
