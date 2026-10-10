"""The ranked queue of open cases, and admin rescoring."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.api.deps import current_org, current_user, get_db, require_admin
from casefile.db.models import Account, AccountCase, AppUser, Assessment, CaseKind, CaseStatus, Org
from casefile.worker.jobs import enqueue_recompute

router = APIRouter(tags=["queue"])


def assessment_view(a: Assessment) -> dict[str, Any]:
    b = a.breakdown or {}
    return {
        "id": str(a.id),
        "as_of": a.as_of.isoformat(),
        "risk": a.risk_score,
        "opportunity": a.opportunity_score,
        "confidence": a.confidence,
        "sources_missing": (b.get("confidence") or {}).get("sources_missing", []),
        "priority": a.priority,
        "opportunity_priority": b.get("opportunity_priority", 0),
        "arr": (b.get("arr") or {}).get("value"),
        "arr_assumed": (b.get("arr") or {}).get("assumed", False),
        "days_to_renewal": (b.get("urgency") or {}).get("days_to_renewal"),
        "urgency_factor": (b.get("urgency") or {}).get("factor"),
        "risk_lines": (b.get("risk") or {}).get("lines", []),
        "opportunity_lines": (b.get("opportunity") or {}).get("lines", []),
        "signals": b.get("signals", []),
        "blind_spots": b.get("blind_spots", []),
        "versions": {
            "engine": a.engine_version,
            "scoring": b.get("scoring_version"),
            "thresholds": b.get("thresholds_version"),
        },
        "explanation_status": a.status.value,
        "headline": a.headline,
        "claims": a.claims or [],
        "explanation_model": a.model,
        "removed_by_checker": len((a.checker_report or {}).get("removed", [])),
        "blind_spot_note": (a.checker_report or {}).get("blind_spot_note") or "",
    }


@router.get("/queue")
def queue(
    kind: Literal["risk", "expansion"] = "risk",
    owner: str = "all",
    db: Session = Depends(get_db),
    user: AppUser = Depends(current_user),
    org: Org = Depends(current_org),
) -> dict[str, Any]:
    query = (
        select(AccountCase, Assessment, Account, AppUser.name)
        .join(Assessment, Assessment.id == AccountCase.current_assessment_id)
        .join(Account, Account.id == AccountCase.account_id)
        .outerjoin(AppUser, AppUser.id == AccountCase.owner_user_id)
        .where(
            AccountCase.org_id == org.id,
            AccountCase.kind == CaseKind(kind),
            AccountCase.status == CaseStatus.open,
        )
    )
    if owner == "me":
        query = query.where(AccountCase.owner_user_id == user.id)
    elif owner != "all":
        query = query.where(AccountCase.owner_user_id == uuid.UUID(owner))
    items = []
    for case, a, acc, owner_name in db.execute(query):
        view = assessment_view(a)
        items.append(
            {
                "case_id": str(case.id),
                "opened_at": case.opened_at.isoformat(),
                "account": {"id": str(acc.id), "name": acc.name, "domains": acc.domains},
                "owner": owner_name,
                **view,
            }
        )
    key = "priority" if kind == "risk" else "opportunity_priority"
    items.sort(key=lambda i: (i[key] or 0, i["risk"] if kind == "risk" else i["opportunity"]), reverse=True)
    for rank, item in enumerate(items, 1):
        item["rank"] = rank
    owners = db.execute(
        select(AppUser.id, AppUser.name)
        .where(AppUser.org_id == org.id, AppUser.crm_owner_id.is_not(None))
        .order_by(AppUser.name)
    ).all()
    return {
        "kind": kind,
        "items": items,
        "revenue_at_stake": sum(i["arr"] or 0 for i in items),
        "owners": [{"id": str(i), "name": n} for i, n in owners],
    }


@router.post("/admin/recompute")
def request_recompute(
    db: Session = Depends(get_db), user: AppUser = Depends(require_admin)
) -> dict[str, int]:
    ids = list(db.scalars(select(Account.id).where(Account.org_id == user.org_id)))
    queued = enqueue_recompute(db, user.org_id, ids)
    db.commit()
    return {"queued": queued}
