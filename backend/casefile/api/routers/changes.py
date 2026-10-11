"""Change sets and approvals: the approval inbox, per-line decisions, and executor status.

The API process never holds write credentials. Approving a line only records the decision and
queues it; the separate executor process performs the write.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.actions.approvals import (
    ApprovalError,
    approve_item,
    approve_tier1,
    can_decide,
    refresh_conflict,
    reject_item,
    retry_item,
)
from casefile.actions.planner import OPEN_SET, plan_case
from casefile.actions.spec import SPECS
from casefile.ai.gateway import AiGateway
from casefile.api.deps import current_org, current_user, get_db
from casefile.config import get_settings
from casefile.connectors.hubspot.live import HubSpotLive
from casefile.db.models import (
    Account,
    AccountCase,
    Approval,
    AppUser,
    Assessment,
    ChangeItem,
    ChangeSet,
    Connection,
    ConnectionKind,
    ExecutionAttempt,
    Org,
    UserRole,
    Zone,
)

router = APIRouter(tags=["changes"])
HEARTBEAT_STALE = timedelta(minutes=2)


class DecisionIn(BaseModel):
    edits: dict[str, Any] | None = None
    comment: str | None = None


def _live() -> HubSpotLive | None:
    token = get_settings().read_hubspot_token
    return HubSpotLive(token.get_secret_value()) if token else None


def _portal(db: Session, org: Org) -> str | None:
    conn = db.scalar(
        select(Connection).where(
            Connection.org_id == org.id,
            Connection.kind == ConnectionKind.hubspot,
            Connection.zone == Zone.write,
        )
    )
    return (conn.config or {}).get("portal_id") if conn else None


def _links(item: ChangeItem, portal: str | None) -> dict[str, str]:
    ref, out = item.target_ref or {}, {}
    if item.target_system == "hubspot" and portal:
        if ref.get("company"):
            out["company"] = f"https://app.hubspot.com/contacts/{portal}/record/0-2/{ref['company']}"
        if ref.get("deal"):
            out["deal"] = f"https://app.hubspot.com/contacts/{portal}/record/0-3/{ref['deal']}"
    if item.target_system == "gmail":
        out["drafts"] = "https://mail.google.com/mail/u/0/#drafts"
    return out


def item_view(db: Session, item: ChangeItem, portal: str | None) -> dict[str, Any]:
    spec = SPECS[item.action_type]
    approvals = db.scalars(
        select(Approval).where(Approval.change_item_id == item.id).order_by(Approval.decided_at)
    ).all()
    users = {u.id: u.name for u in db.scalars(select(AppUser).where(AppUser.org_id == item.org_id))}
    attempts = db.scalars(
        select(ExecutionAttempt)
        .where(ExecutionAttempt.change_item_id == item.id)
        .order_by(ExecutionAttempt.attempt_no)
    ).all()
    return {
        "id": str(item.id),
        "seq": item.seq,
        "action_type": item.action_type,
        "label": spec.label,
        "tier": item.tier,
        "target_system": item.target_system,
        "target_ref": item.target_ref,
        "before": item.before,
        "proposed": item.proposed_payload,
        "final": item.final_payload,
        "editable": sorted(spec.editable),
        "field_update": spec.field_update,
        "status": item.status.value,
        "blocked_reason": (item.external_result or {}).get("blocked"),
        "result": item.external_result,
        "why": item.why,
        "evidence_count": len(item.evidence_ids or []),
        "payload_hash": item.payload_hash,
        "links": _links(item, portal),
        "approvals": [
            {
                "decision": a.decision.value,
                "by": users.get(a.user_id, "unknown"),
                "edited": a.edited,
                "at": a.decided_at.isoformat(),
                "comment": a.comment,
                "hash": (a.approved_payload_hash or "")[:12],
                "channel": a.channel.value,
            }
            for a in approvals
        ],
        "attempts": [
            {
                "no": a.attempt_no,
                "status": a.status,
                "started_at": a.started_at.isoformat(),
                "finished_at": a.finished_at.isoformat() if a.finished_at else None,
                "error": a.error,
                "external_id": a.external_id,
            }
            for a in attempts
        ],
    }


def set_view(db: Session, cs: ChangeSet, user: AppUser, org: Org) -> dict[str, Any]:
    case = db.get(AccountCase, cs.case_id)
    assert case is not None
    account = db.get(Account, case.account_id)
    assert account is not None
    portal = _portal(db, org)
    items = db.scalars(
        select(ChangeItem).where(ChangeItem.change_set_id == cs.id).order_by(ChangeItem.seq)
    ).all()
    return {
        "id": str(cs.id),
        "case_id": str(case.id),
        "case_kind": case.kind.value,
        "account": {"id": str(account.id), "name": account.name},
        "play": cs.playbook_id,
        "playbook_version": cs.playbook_version,
        "status": cs.status.value,
        "assessment_id": str(cs.assessment_id) if cs.assessment_id else None,
        "notes": cs.notes,
        "created_at": cs.created_at.isoformat(),
        "can_decide": can_decide(user, case),
        "items": [item_view(db, i, portal) for i in items],
    }


def _http(exc: ApprovalError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


def _set_for_item(db: Session, item: ChangeItem) -> ChangeSet:
    cs = db.get(ChangeSet, item.change_set_id)
    assert cs is not None
    return cs


@router.get("/inbox")
def inbox(
    db: Session = Depends(get_db), user: AppUser = Depends(current_user), org: Org = Depends(current_org)
) -> list[dict[str, Any]]:
    q = (
        select(ChangeSet, AccountCase, Assessment)
        .join(AccountCase, AccountCase.id == ChangeSet.case_id)
        .outerjoin(Assessment, Assessment.id == AccountCase.current_assessment_id)
        .where(
            ChangeSet.org_id == org.id, ChangeSet.status.in_([*OPEN_SET, "approved", "executing", "failed"])
        )
    )
    if user.role != UserRole.admin:
        q = q.where(AccountCase.owner_user_id == user.id)
    rows = sorted(db.execute(q).all(), key=lambda r: -(r[2].priority if r[2] else 0))
    out = []
    for cs, case, a in rows:
        account = db.get(Account, case.account_id)
        items = db.scalars(select(ChangeItem).where(ChangeItem.change_set_id == cs.id)).all()
        counts: dict[str, int] = {}
        for i in items:
            counts[i.status.value] = counts.get(i.status.value, 0) + 1
        out.append(
            {
                "id": str(cs.id),
                "case_id": str(case.id),
                "case_kind": case.kind.value,
                "account": {"id": str(case.account_id), "name": account.name if account else "?"},
                "headline": a.headline if a else None,
                "score": (a.risk_score if case.kind.value == "risk" else a.opportunity_score) if a else None,
                "play": cs.playbook_id,
                "status": cs.status.value,
                "counts": counts,
                "created_at": cs.created_at.isoformat(),
            }
        )
    return out


@router.get("/cases/{case_id}/plan")
def get_plan(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: AppUser = Depends(current_user),
    org: Org = Depends(current_org),
) -> dict[str, Any] | None:
    case = db.get(AccountCase, case_id)
    if case is None or case.org_id != org.id:
        raise HTTPException(404, "case not found")
    cs = db.scalar(
        select(ChangeSet)
        .where(ChangeSet.case_id == case.id, ChangeSet.status != "superseded")
        .order_by(ChangeSet.created_at.desc())
        .limit(1)
    )
    return set_view(db, cs, user, org) if cs else None


@router.post("/cases/{case_id}/plan")
def make_plan(
    case_id: uuid.UUID,
    force: bool = False,
    db: Session = Depends(get_db),
    user: AppUser = Depends(current_user),
    org: Org = Depends(current_org),
) -> dict[str, Any]:
    case = db.get(AccountCase, case_id)
    if case is None or case.org_id != org.id:
        raise HTTPException(404, "case not found")
    if not can_decide(user, case):
        raise HTTPException(403, "only the account owner or an admin can plan this case")
    result = plan_case(db, case.id, gateway=AiGateway(db, org.id), live=_live(), force=force)
    db.commit()
    if result.change_set is None:
        raise HTTPException(409, result.reason or "could not plan this case")
    return set_view(db, result.change_set, user, org)


@router.post("/change-items/{item_id}/approve")
def approve(
    item_id: uuid.UUID,
    body: DecisionIn | None = None,
    db: Session = Depends(get_db),
    user: AppUser = Depends(current_user),
    org: Org = Depends(current_org),
) -> dict[str, Any]:
    try:
        item = approve_item(
            db, user, item_id, edits=body.edits if body else None, comment=body.comment if body else None
        )
    except ApprovalError as exc:
        db.rollback()
        raise _http(exc) from exc
    db.commit()
    return set_view(db, _set_for_item(db, item), user, org)


@router.post("/change-items/{item_id}/reject")
def reject(
    item_id: uuid.UUID,
    body: DecisionIn | None = None,
    db: Session = Depends(get_db),
    user: AppUser = Depends(current_user),
    org: Org = Depends(current_org),
) -> dict[str, Any]:
    try:
        item = reject_item(db, user, item_id, comment=body.comment if body else None)
    except ApprovalError as exc:
        db.rollback()
        raise _http(exc) from exc
    db.commit()
    return set_view(db, _set_for_item(db, item), user, org)


@router.post("/change-sets/{set_id}/approve-tier1")
def approve_all_tier1(
    set_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: AppUser = Depends(current_user),
    org: Org = Depends(current_org),
) -> dict[str, Any]:
    try:
        approve_tier1(db, user, set_id)
    except ApprovalError as exc:
        db.rollback()
        raise _http(exc) from exc
    db.commit()
    cs = db.get(ChangeSet, set_id)
    assert cs is not None
    return set_view(db, cs, user, org)


@router.post("/change-items/{item_id}/retry")
def retry(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: AppUser = Depends(current_user),
    org: Org = Depends(current_org),
) -> dict[str, Any]:
    try:
        item = retry_item(db, user, item_id)
    except ApprovalError as exc:
        db.rollback()
        raise _http(exc) from exc
    db.commit()
    return set_view(db, _set_for_item(db, item), user, org)


@router.post("/change-items/{item_id}/refresh")
def refresh(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: AppUser = Depends(current_user),
    org: Org = Depends(current_org),
) -> dict[str, Any]:
    try:
        item = refresh_conflict(db, user, item_id, _live())
    except ApprovalError as exc:
        db.rollback()
        raise _http(exc) from exc
    db.commit()
    return set_view(db, _set_for_item(db, item), user, org)


@router.get("/executor/status")
def executor_status(db: Session = Depends(get_db), org: Org = Depends(current_org)) -> dict[str, Any]:
    now = datetime.now(UTC)
    conns = db.scalars(
        select(Connection).where(Connection.org_id == org.id, Connection.zone == Zone.write)
    ).all()
    beats = [c.last_success_at for c in conns if c.last_success_at]
    last = max(beats) if beats else None
    return {
        "running": bool(last and now - last < HEARTBEAT_STALE),
        "last_heartbeat": last.isoformat() if last else None,
        "shadow": any((c.cursor or {}).get("shadow") for c in conns),
        "targets": [
            {
                "kind": c.kind.value,
                "name": c.name,
                "config": {k: v for k, v in (c.config or {}).items() if k != "secret"},
                "writer_loaded": bool((c.cursor or {}).get("writer")),
            }
            for c in conns
        ],
    }
