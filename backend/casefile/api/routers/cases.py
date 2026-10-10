"""Evidence drawer, owner feedback on cases, and AI usage."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.api.deps import current_org, current_user, get_db
from casefile.audit.log import append as audit
from casefile.config import get_settings
from casefile.db.models import AccountCase, Activity, AppUser, CaseStatus, EmailLabel, LlmCall, Org

router = APIRouter(tags=["cases"])
PURGED_NOTE = "Full text deleted after the retention period; showing the stored snippet."


@router.get("/evidence/activity/{activity_id}")
def evidence_activity(
    activity_id: uuid.UUID, db: Session = Depends(get_db), org: Org = Depends(current_org)
) -> dict[str, Any]:
    a = db.get(Activity, activity_id)
    if a is None or a.org_id != org.id:
        raise HTTPException(404, "record not found")
    purged = a.body_text is None
    labels = db.scalars(select(EmailLabel).where(EmailLabel.activity_id == a.id)).all()
    return {
        "id": str(a.id),
        "type": a.type.value,
        "direction": a.direction.value,
        "source": a.source,
        "at": a.occurred_at.isoformat(),
        "subject": a.subject,
        "from": a.from_email,
        "to": a.to_emails,
        "cc": a.cc_emails,
        "participants": a.participant_emails,
        "body": a.snippet if purged else a.body_text,
        "body_note": PURGED_NOTE if purged else None,
        "cancelled": a.cancelled_at is not None,
        "hidden_text_present": bool((a.extra or {}).get("hidden_text")),
        "injection": {"flagged": a.injection_flag, "reason": a.injection_reason, "quote": a.injection_quote},
        "labels": [
            {
                "label": lab.label,
                "status": lab.status.value,
                "confidence": round(lab.confidence, 2),
                "quote": lab.quote,
                "model": lab.model,
            }
            for lab in labels
        ],
        "match": {"method": a.match_method.value, "confidence": a.match_confidence},
    }


class FeedbackIn(BaseModel):
    verdict: Literal["useful", "wrong"]
    note: str = ""


@router.post("/cases/{case_id}/feedback")
def case_feedback(
    case_id: uuid.UUID, body: FeedbackIn, db: Session = Depends(get_db), user: AppUser = Depends(current_user)
) -> dict[str, Any]:
    case = db.get(AccountCase, case_id)
    if case is None or case.org_id != user.org_id:
        raise HTTPException(404, "case not found")
    case.feedback, case.feedback_note = f"owner:{body.verdict}", body.note[:2000] or None
    if body.verdict == "wrong":
        case.status = CaseStatus.wrong
        case.resolved_at = datetime.now(UTC)
    audit(
        db,
        user.org_id,
        "case_feedback",
        actor_type="user",
        actor_id=str(user.id),
        entity_type="case",
        entity_id=str(case.id),
        details={"verdict": body.verdict, "note": body.note[:500]},
    )
    db.commit()
    return {"status": case.status.value, "feedback": case.feedback}


@router.get("/ai/usage")
def ai_usage(db: Session = Depends(get_db), org: Org = Depends(current_org)) -> dict[str, Any]:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.execute(
        select(
            LlmCall.purpose,
            LlmCall.model,
            func.count(),
            func.sum(LlmCall.cost_usd),
            func.sum(LlmCall.input_tokens),
            func.sum(LlmCall.output_tokens),
            func.count().filter(LlmCall.ok.is_(False)),
        )
        .where(LlmCall.org_id == org.id)
        .group_by(LlmCall.purpose, LlmCall.model)
    ).all()
    today = db.scalar(
        select(func.coalesce(func.sum(LlmCall.cost_usd), 0)).where(
            LlmCall.org_id == org.id, LlmCall.created_at >= start
        )
    )
    settings = get_settings()
    return {
        "enabled": settings.ai_enabled,
        "budget_usd_per_day": settings.ai_daily_budget_usd,
        "spent_today_usd": float(today or 0),
        "by_purpose": [
            {
                "purpose": p,
                "model": m,
                "calls": n,
                "cost_usd": float(c or 0),
                "avg_cost_usd": round(float(c or 0) / n, 5) if n else 0,
                "input_tokens": int(i or 0),
                "output_tokens": int(o or 0),
                "failed": int(failed or 0),
            }
            for p, m, n, c, i, o, failed in rows
        ],
    }
