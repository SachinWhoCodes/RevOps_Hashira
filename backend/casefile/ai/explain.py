"""Glue between assessments and the case writer: write, store, audit, and re-check explanations."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.ai.case_writer import Written, template_explanation, write_explanation
from casefile.ai.checker import check_explanation
from casefile.ai.evidence import build_pack
from casefile.ai.gateway import AiGateway
from casefile.audit.log import append as audit
from casefile.cases.service import Assessed, RecomputeContext, assess
from casefile.config import get_settings
from casefile.db.models import Account, AccountCase, AppUser, Assessment, AssessmentStatus, CaseStatus, Org
from casefile.timeline.snapshot import AccountHistory, Snapshot
from casefile.worker.jobs import enqueue


def _points(assessed: Assessed) -> dict[str, int]:
    b = assessed.score.breakdown
    return {line["signal"]: line["points"] for line in b["risk"]["lines"] + b["opportunity"]["lines"]}


def _owner_name(session: Session, account: Account) -> str | None:
    user = session.get(AppUser, account.owner_user_id) if account.owner_user_id else None
    return user.name if user else None


def store(session: Session, a: Assessment, written: Written) -> None:
    a.headline, a.claims = written.headline, written.claims
    a.status = AssessmentStatus.verified if written.status == "verified" else AssessmentStatus.fallback
    a.checker_report = {**written.report, "blind_spot_note": written.blind_spot_note}
    a.model, a.prompt_version = written.model, written.prompt_version
    removed = len(written.report.get("removed", []))
    audit(
        session,
        a.org_id,
        "explanation_written",
        actor_type="agent",
        entity_type="assessment",
        entity_id=str(a.id),
        evidence_ids=sorted({e["id"] for c in written.claims for e in c["evidence"]}),
        details={
            "status": written.status,
            "claims": len(written.claims),
            "removed_by_checker": removed,
            "reason": written.report.get("reason"),
        },
        model=written.model,
        prompt_version=written.prompt_version,
    )


def explain_assessment(
    session: Session, assessment_id: uuid.UUID, gateway: AiGateway | None
) -> Written | None:
    a = session.get(Assessment, assessment_id)
    if a is None:
        return None
    account = session.get(Account, a.account_id)
    org = session.get(Org, a.org_id)
    assert account is not None and org is not None
    snap = AccountHistory.load(session, account.id).as_of(a.as_of)
    if snap is None:
        return None
    ctx = RecomputeContext.build(session, org)
    assessed = assess(
        snap, arr_fallback=ctx.arr_median, ambiguous_records=ctx.ambiguous_by_account.get(str(account.id), 0)
    )
    if assessed.score.signature != a.signature:
        return None  # this assessment was superseded; the newer one gets its own explanation
    owner = _owner_name(session, account)
    if gateway is None:
        pack = build_pack(
            snap, assessed.hits, owner_name=owner, risk=a.risk_score, opportunity=a.opportunity_score
        )
        written = template_explanation(
            assessed.hits, pack, risk=a.risk_score, opportunity=a.opportunity_score, reason="ai_not_used"
        )
    else:
        written = write_explanation(
            gateway,
            snap,
            assessed.hits,
            risk=a.risk_score,
            opportunity=a.opportunity_score,
            confidence=a.confidence,
            blind_spots=assessed.blind_spots,
            points=_points(assessed),
            owner_name=owner,
            account_id=account.id,
        )
    store(session, a, written)
    if get_settings().auto_plan_cases:
        for case_id in session.scalars(
            select(AccountCase.id).where(
                AccountCase.current_assessment_id == a.id, AccountCase.status == CaseStatus.open
            )
        ):
            enqueue(
                session, "plan_case", {"case_id": str(case_id)}, org_id=a.org_id, dedupe_key=f"plan:{case_id}"
            )
    return written


def recheck_stored(session: Session, a: Assessment, snap: Snapshot, assessed: Assessed) -> bool:
    """Re-run the checker on a stored explanation against today's facts (no AI, no cost).
    Day counts move, so a verified claim can go stale; stale claims are removed.
    Returns True when nothing usable is left and the explanation should be rewritten."""
    if not a.claims:
        return True
    account = session.get(Account, a.account_id)
    assert account is not None
    pack = build_pack(
        snap,
        assessed.hits,
        owner_name=_owner_name(session, account),
        risk=a.risk_score,
        opportunity=a.opportunity_score,
    )
    claims = [
        {"text": c["text"], "evidence_ids": [e["eid"] for e in c["evidence"]], "fact_keys": c["fact_keys"]}
        for c in a.claims
    ]
    report = check_explanation(a.headline or "", claims, "", pack)
    if not report.failed and not report.headline_reasons:
        return False
    keep = {v.text for v in report.passed}
    a.claims = [c for c in a.claims if c["text"] in keep]
    stale: list[dict[str, Any]] = [{"text": v.text, "reasons": v.reasons} for v in report.failed]
    a.checker_report = {**(a.checker_report or {}), "stale_removed": stale}
    return not a.claims


def open_case_assessments(session: Session, org: Org) -> list[uuid.UUID]:
    return [
        aid
        for aid in session.scalars(
            select(AccountCase.current_assessment_id).where(
                AccountCase.org_id == org.id, AccountCase.status == CaseStatus.open
            )
        )
        if aid is not None
    ]
