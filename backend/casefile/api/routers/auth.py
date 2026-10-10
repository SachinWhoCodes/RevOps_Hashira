from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.api.deps import current_user, get_db
from casefile.bootstrap import verify_password
from casefile.db.models import AppUser

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str


def _out(user: AppUser) -> UserOut:
    return UserOut(id=str(user.id), email=user.email, name=user.name, role=user.role.value)


@router.post("/login")
async def login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> UserOut:
    user = db.scalar(select(AppUser).where(AppUser.email == body.email.strip().lower()))
    if user is None or not user.is_active or not verify_password(user.password_hash, body.password):
        await asyncio.sleep(0.5)  # slow down guessing
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong email or password")
    request.session.clear()
    request.session["uid"] = str(user.id)
    return _out(user)


@router.post("/logout")
def logout(request: Request) -> dict[str, bool]:
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(user: AppUser = Depends(current_user)) -> UserOut:
    return _out(user)
