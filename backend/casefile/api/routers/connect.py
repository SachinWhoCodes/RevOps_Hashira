"""Self-service connections (admin only): HubSpot, Google (Gmail + Calendar), Slack. See casefile.connect.

Credentials go in, never come out: responses describe connections, not their secrets.
"""

from __future__ import annotations

import urllib.parse
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from casefile import connect as cx
from casefile import onboarding as ob
from casefile.api.deps import current_org, get_db, require_admin
from casefile.config import get_settings
from casefile.db.models import AppUser, Org

router = APIRouter(tags=["connect"])


class TokenIn(BaseModel):
    token: str


class SlackIn(BaseModel):
    bot_token: str
    app_token: str


class GoogleClientIn(BaseModel):
    client_id: str
    client_secret: str


class GoogleStartIn(BaseModel):
    purpose: Literal["read", "drafts"]


def _fail(exc: Exception) -> HTTPException:
    return HTTPException(422, str(exc))


@router.get("/connect")
def overview(
    db: Session = Depends(get_db), org: Org = Depends(current_org), _: AppUser = Depends(require_admin)
) -> dict[str, Any]:
    return cx.overview(db, org)


@router.post("/connect/hubspot/read")
def hubspot_read(
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


@router.post("/connect/hubspot/write")
def hubspot_write(
    body: TokenIn,
    db: Session = Depends(get_db),
    org: Org = Depends(current_org),
    admin: AppUser = Depends(require_admin),
) -> dict[str, Any]:
    try:
        out = cx.connect_hubspot_write(db, org, admin, body.token)
    except cx.ConnectError as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return out


@router.post("/connect/slack")
def slack(
    body: SlackIn,
    db: Session = Depends(get_db),
    org: Org = Depends(current_org),
    admin: AppUser = Depends(require_admin),
) -> dict[str, Any]:
    try:
        out = cx.connect_slack(db, org, admin, body.bot_token, body.app_token)
    except cx.ConnectError as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return out


@router.get("/connect/slack/manifest")
def slack_manifest(_: AppUser = Depends(require_admin)) -> JSONResponse:
    return JSONResponse(
        cx.SLACK_MANIFEST,
        headers={"Content-Disposition": 'attachment; filename="casefile-slack-manifest.json"'},
    )


@router.put("/connect/google/client")
def google_client(
    body: GoogleClientIn,
    db: Session = Depends(get_db),
    org: Org = Depends(current_org),
    admin: AppUser = Depends(require_admin),
) -> dict[str, bool]:
    try:
        cx.set_own_google_client(db, org, admin, body.client_id, body.client_secret)
    except cx.ConnectError as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return {"saved": True}


@router.delete("/connect/google/client")
def clear_google_client(
    db: Session = Depends(get_db), org: Org = Depends(current_org), admin: AppUser = Depends(require_admin)
) -> dict[str, bool]:
    cx.clear_own_google_client(db, org, admin)
    db.commit()
    return {"cleared": True}


@router.post("/connect/google/start")
def google_start(
    body: GoogleStartIn, org: Org = Depends(current_org), admin: AppUser = Depends(require_admin)
) -> dict[str, str]:
    try:
        return {"url": cx.google_start(org, admin, body.purpose)}
    except cx.ConnectError as exc:
        raise _fail(exc) from exc


@router.get("/oauth/google/callback")
def google_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Google sends the browser back here. The result is shown on the Connections page."""
    back = f"{get_settings().app_base_url.rstrip('/')}/connections"
    if error or not code or not state:
        reason = (
            "you cancelled the Google sign-in" if error == "access_denied" else (error or "no code returned")
        )
        return RedirectResponse(f"{back}?google_error={urllib.parse.quote(reason)}", status_code=303)
    try:
        out = cx.google_finish(db, request.session.get("uid"), code, state)
    except cx.ConnectError as exc:
        db.rollback()
        return RedirectResponse(f"{back}?google_error={urllib.parse.quote(str(exc))}", status_code=303)
    db.commit()
    query = urllib.parse.urlencode({"google": out["purpose"], "mailbox": out["mailbox"]})
    return RedirectResponse(f"{back}?{query}", status_code=303)


@router.delete("/connect/{connection_id}")
def disconnect(
    connection_id: uuid.UUID,
    db: Session = Depends(get_db),
    org: Org = Depends(current_org),
    admin: AppUser = Depends(require_admin),
) -> dict[str, bool]:
    try:
        cx.disconnect(db, org, admin, connection_id)
    except cx.ConnectError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.commit()
    return {"disconnected": True}
