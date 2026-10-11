"""Onboarding (C4): the admin's checklist from first login to live. See casefile.onboarding."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from casefile import onboarding as ob
from casefile.api.deps import current_org, get_db, require_admin
from casefile.db.models import AppUser, Org

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

USAGE_TEMPLATE = (
    "account_key,date,active_users,key_actions,seats_used,departments\n"
    'acme.com,2026-09-01,42,310,50,"{""Sales"": 30, ""Ops"": 12}"\n'
    "acme.com,2026-09-02,40,295,50,\n"
)


class CompanyIn(BaseModel):
    name: str
    timezone: str
    internal_domains: list[str]


class PermissionIn(BaseModel):
    statements: list[str]


class TokenIn(BaseModel):
    token: str


class ModeIn(BaseModel):
    to: Literal["setup", "shadow", "live"]
    reason: str | None = None


def _fail(exc: ob.OnboardingError) -> HTTPException:
    return HTTPException(422, str(exc))


@router.get("")
def get_status(
    db: Session = Depends(get_db), org: Org = Depends(current_org), _: AppUser = Depends(require_admin)
) -> dict[str, Any]:
    return ob.status(db, org)


@router.put("/company")
def put_company(
    body: CompanyIn,
    db: Session = Depends(get_db),
    org: Org = Depends(current_org),
    admin: AppUser = Depends(require_admin),
) -> dict[str, Any]:
    try:
        changed = ob.update_company(
            db, org, admin, name=body.name, timezone=body.timezone, internal_domains=body.internal_domains
        )
    except ob.OnboardingError as exc:
        raise _fail(exc) from exc
    db.commit()
    return {"changed": changed}


@router.post("/permission")
def post_permission(
    body: PermissionIn,
    db: Session = Depends(get_db),
    org: Org = Depends(current_org),
    admin: AppUser = Depends(require_admin),
) -> dict[str, Any]:
    try:
        record = ob.record_permission(db, org, admin, body.statements)
    except ob.OnboardingError as exc:
        raise _fail(exc) from exc
    db.commit()
    return record


@router.post("/hubspot")
def post_hubspot(
    body: TokenIn,
    db: Session = Depends(get_db),
    org: Org = Depends(current_org),
    admin: AppUser = Depends(require_admin),
) -> dict[str, Any]:
    try:
        out = ob.connect_hubspot(db, org, admin, body.token)
    except ob.OnboardingError as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return out


@router.get("/usage/template", response_class=PlainTextResponse)
def usage_template(_: AppUser = Depends(require_admin)) -> PlainTextResponse:
    return PlainTextResponse(
        USAGE_TEMPLATE,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="casefile-usage-template.csv"'},
    )


@router.post("/usage")
async def post_usage(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    org: Org = Depends(current_org),
    admin: AppUser = Depends(require_admin),
) -> dict[str, Any]:
    content = await file.read(ob.USAGE_MAX_BYTES + 1)
    try:
        out = ob.upload_usage(db, org, admin, file.filename or "usage.csv", content)
    except ob.OnboardingError as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return out


@router.post("/usage/skip")
def post_usage_skip(
    db: Session = Depends(get_db), org: Org = Depends(current_org), admin: AppUser = Depends(require_admin)
) -> dict[str, bool]:
    ob.skip_usage(db, org, admin)
    db.commit()
    return {"skipped": True}


@router.post("/mode")
def post_mode(
    body: ModeIn,
    db: Session = Depends(get_db),
    org: Org = Depends(current_org),
    admin: AppUser = Depends(require_admin),
) -> dict[str, Any]:
    current = ob.org_mode(org)
    try:
        if body.to == "shadow" and current == "setup":
            ob.start_shadow(db, org, admin)
        elif body.to == "shadow":
            ob.back_to_shadow(db, org, admin, body.reason)
        elif body.to == "live":
            ob.go_live(db, org, admin, body.reason)
        else:
            ob.back_to_setup(db, org, admin, body.reason)
    except ob.OnboardingError as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return {"mode": ob.org_mode(org)}
