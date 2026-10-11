"""Assessments and case lifecycle.

``assess`` is pure (snapshot in, signals + score + blind spots out) and is what the live queue, the
as-of time machine and the back-test all call. ``recompute_account`` persists the result:

- A new ``assessment`` row is written only when the signals/evidence or the scores change; otherwise
  the latest row is refreshed in place (day counts and urgency move every day, evidence does not).
- A risk case opens at ``open_risk_at`` and auto-resolves below ``close_risk_below``; the gap between
  the two stops a case flapping open and closed on small changes. Same for expansion cases.
- Case and assessment events go to the hash-chained audit log.

The per-owner "new cases per day" cap from the plan is applied where people are interrupted — the
Phase 4 morning digest — not here: the queue is ranked, so a cap would only hide information.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.audit.log import append as audit
from casefile.cases.blind_spots import account_blind_spots
from casefile.db.models import (
    Account,
    AccountCase,
    Activity,
    Assessment,
    AssessmentStatus,
    CaseKind,
    CaseStatus,
    Deal,
    DealType,
    MatchMethod,
    Org,
    Signal,
)
from casefile.scoring.points import Score, default_scoring, score
from casefile.signals.base import ENGINE_VERSION, SignalHit, default_thresholds, run_signals
from casefile.timeline.snapshot import AccountHistory, Snapshot
from casefile.worker.jobs import enqueue


@dataclass(frozen=True)
class Assessed:
    hits: list[SignalHit]
    score: Score
    blind_spots: list[dict[str, Any]]

    def breakdown(self) -> dict[str, Any]:
        return {
            **self.score.breakdown,
            "signals": [h.to_dict() for h in self.hits],
            "blind_spots": self.blind_spots,
            "thresholds_version": default_thresholds()["version"],
        }


def assess(
    snap: Snapshot,
    *,
    thresholds: dict[str, Any] | None = None,
    scoring: dict[str, Any] | None = None,
    arr_fallback: Decimal | None = None,
    ambiguous_records: int = 0,
) -> Assessed:
    hits = run_signals(snap, thresholds)
    return Assessed(
        hits=hits,
        score=score(snap, hits, scoring, arr_fallback=arr_fallback),
        blind_spots=account_blind_spots(snap, ambiguous_records=ambiguous_records),
    )


@dataclass
class RecomputeContext:
    """Org-wide values computed once per batch."""

    org: Org
    arr_median: Decimal | None
    ambiguous_by_account: dict[str, int]

    @classmethod
    def build(cls, session: Session, org: Org) -> RecomputeContext:
        amounts = [
            a
            for a in session.scalars(
                select(Deal.amount).where(
                    Deal.org_id == org.id, Deal.deal_type == DealType.renewal, Deal.amount.is_not(None)
                )
            )
            if a is not None
        ]
        counts: dict[str, int] = {}
        for candidates in session.scalars(
            select(Activity.match_candidates).where(
                Activity.org_id == org.id, Activity.match_method == MatchMethod.ambiguous
            )
        ):
            for c in candidates or []:
                counts[c] = counts.get(c, 0) + 1
        return cls(org, Decimal(statistics.median(amounts)) if amounts else None, counts)


@dataclass
class RecomputeResult:
    account_id: uuid.UUID
    status: str  # assessed | unchanged | inactive
    assessment_id: uuid.UUID | None = None
    opened: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    explain_queued: bool = False


def _open_case(session: Session, account_id: uuid.UUID, kind: CaseKind) -> AccountCase | None:
    return session.scalar(
        select(AccountCase).where(
            AccountCase.account_id == account_id,
            AccountCase.kind == kind,
            AccountCase.status.in_([CaseStatus.open, CaseStatus.snoozed]),
        )
    )


def _versions(a: Assessment) -> tuple[Any, Any, Any]:
    b = a.breakdown or {}
    return (a.engine_version, b.get("scoring_version"), b.get("thresholds_version"))


def _opened_under_other_rules(
    case: AccountCase, assessments: dict[uuid.UUID, Assessment], versions: tuple[Any, Any, Any]
) -> bool:
    prior = assessments.get(case.current_assessment_id) if case.current_assessment_id else None
    return prior is not None and _versions(prior) != versions


def _resolve(session: Session, case: AccountCase, reason: str, now: datetime) -> None:
    # Short machine code in `feedback` (also used for owner feedback later); full reason in the note.
    case.status, case.resolved_at = CaseStatus.resolved, now
    case.feedback, case.feedback_note = "auto_resolved", reason
    audit(
        session,
        case.org_id,
        "case_resolved",
        entity_type="case",
        entity_id=str(case.id),
        details={"kind": case.kind.value, "reason": reason},
    )


def recompute_account(
    session: Session,
    account_id: uuid.UUID,
    ctx: RecomputeContext,
    *,
    as_of: datetime | None = None,
) -> RecomputeResult:
    now = as_of or datetime.now(UTC)
    account = session.get(Account, account_id)
    assert account is not None
    snap = AccountHistory.load(session, account_id).as_of(now)
    result = RecomputeResult(account_id, "assessed")

    no_deal = session.scalar(select(Deal.id).where(Deal.account_id == account_id).limit(1)) is None
    if snap is None or snap.is_churned() or no_deal:
        # No deal: a prospect, a vendor, or a company the CRM created from an email — not a customer to watch.
        reason = (
            "auto: not a customer (no deal in the CRM)" if no_deal else "auto: account inactive or churned"
        )
        for kind in CaseKind:
            if (case := _open_case(session, account_id, kind)) is not None:
                _resolve(session, case, reason, now)
                result.resolved.append(kind.value)
        result.status = "inactive"
        return result

    a = assess(
        snap, arr_fallback=ctx.arr_median, ambiguous_records=ctx.ambiguous_by_account.get(str(account_id), 0)
    )
    s = a.score
    carried = False
    latest = session.scalar(
        select(Assessment)
        .where(Assessment.account_id == account_id)
        .order_by(Assessment.created_at.desc())
        .limit(1)
    )
    versions = (ENGINE_VERSION, s.breakdown["scoring_version"], default_thresholds()["version"])
    unchanged = (
        latest is not None
        and latest.signature == s.signature
        and latest.risk_score == s.risk
        and latest.opportunity_score == s.opportunity
        and _versions(latest) == versions
    )
    if unchanged:
        assert latest is not None
        latest.as_of, latest.priority, latest.confidence = now, s.priority, s.confidence
        latest.breakdown = a.breakdown()
        assessment = latest
        result.status = "unchanged"
    else:
        assessment = Assessment(
            org_id=account.org_id,
            account_id=account_id,
            as_of=now,
            risk_score=s.risk,
            opportunity_score=s.opportunity,
            confidence=s.confidence,
            priority=s.priority,
            breakdown=a.breakdown(),
            status=AssessmentStatus.pending,  # Phase 3 writes and checks the explanation
            engine_version=ENGINE_VERSION,
            signature=s.signature,
            # Set here, not by the DB default (transaction start): "latest assessment" must not tie
            # when one transaction recomputes an account twice.
            created_at=datetime.now(UTC),
        )
        if latest is not None and latest.signature == s.signature and latest.claims:
            # Same signals and evidence, only the rules or scores moved: keep the checked explanation.
            # It is re-checked in code below (free); a paid rewrite happens only if nothing survives.
            assessment.headline, assessment.claims = latest.headline, latest.claims
            assessment.checker_report = latest.checker_report
            assessment.model, assessment.prompt_version = latest.model, latest.prompt_version
            assessment.status = latest.status
            carried = True
        session.add(assessment)
        session.flush()
        for h in a.hits:
            session.add(
                Signal(
                    org_id=account.org_id,
                    account_id=account_id,
                    assessment_id=assessment.id,
                    type=h.type,
                    polarity=h.polarity,
                    value=h.value,
                    facts=h.facts,
                    window_start=h.window_start,
                    window_end=h.window_end,
                    evidence=[e.to_dict() for e in h.evidence],
                    as_of=now,
                    engine_version=ENGINE_VERSION,
                )
            )
        audit(
            session,
            account.org_id,
            "assessment_created",
            entity_type="account",
            entity_id=str(account_id),
            evidence_ids=sorted({e.id for h in a.hits for e in h.evidence}),
            details={
                "risk": s.risk,
                "opportunity": s.opportunity,
                "signals": [h.type for h in a.hits],
                "engine": ENGINE_VERSION,
                "scoring_version": s.breakdown["scoring_version"],
            },
        )
    result.assessment_id = assessment.id

    cfg = default_scoring()["cases"]
    at_risk = s.risk >= cfg["suppress_expansion_when_risk_at"]
    previous = {
        a.id: a for a in session.scalars(select(Assessment).where(Assessment.account_id == account_id))
    }
    for kind, value, open_at, close_below in (
        (CaseKind.risk, s.risk, cfg["open_risk_at"], cfg["close_risk_below"]),
        (CaseKind.expansion, s.opportunity, cfg["open_opportunity_at"], cfg["close_opportunity_below"]),
    ):
        case = _open_case(session, account_id, kind)
        if kind == CaseKind.expansion and at_risk:
            if case is not None:
                _resolve(session, case, "auto: paused while the account is at risk", now)
                result.resolved.append(kind.value)
            continue
        if case is None and value >= open_at:
            case = AccountCase(
                org_id=account.org_id,
                account_id=account_id,
                kind=kind,
                status=CaseStatus.open,
                owner_user_id=account.owner_user_id,
                current_assessment_id=assessment.id,
                opened_at=now,
            )
            session.add(case)
            session.flush()
            audit(
                session,
                account.org_id,
                "case_opened",
                entity_type="case",
                entity_id=str(case.id),
                details={"kind": kind.value, "score": value, "account_id": str(account_id)},
            )
            result.opened.append(kind.value)
        elif case is not None and value < close_below:
            _resolve(session, case, f"auto: {kind.value} score fell to {value}", now)
            result.resolved.append(kind.value)
        elif case is not None and value < open_at and _opened_under_other_rules(case, previous, versions):
            # The hysteresis gap only holds within one rule version; after a rules change an
            # open case must meet the new opening threshold again.
            _resolve(session, case, f"auto: below the threshold of the updated rules ({value})", now)
            result.resolved.append(kind.value)
        elif case is not None:
            case.current_assessment_id = assessment.id

    # Explanations are written only for accounts with an open case (cost control), as a queued job
    # so rescoring stays fast and AI-free. Unchanged assessments are re-checked in code instead.
    has_open_case = any(_open_case(session, account_id, k) is not None for k in CaseKind)
    if has_open_case:
        from casefile.ai.explain import recheck_stored

        needs_writing = (result.status == "assessed" and not carried) or (
            assessment.status == AssessmentStatus.pending
        )
        if not needs_writing and recheck_stored(session, assessment, snap, a):
            needs_writing = True
        if needs_writing:
            enqueue(
                session,
                "write_explanation",
                {"assessment_id": str(assessment.id)},
                org_id=account.org_id,
                dedupe_key=f"explain:{assessment.id}",
            )
            result.explain_queued = True
    return result


def recompute_all(session: Session, org: Org, *, as_of: datetime | None = None) -> dict[str, int]:
    ctx = RecomputeContext.build(session, org)
    totals = {"assessed": 0, "unchanged": 0, "inactive": 0, "opened": 0, "resolved": 0}
    for account_id in session.scalars(
        select(Account.id).where(Account.org_id == org.id).order_by(Account.name)
    ):
        r = recompute_account(session, account_id, ctx, as_of=as_of)
        totals[r.status] += 1
        totals["opened"] += len(r.opened)
        totals["resolved"] += len(r.resolved)
        session.commit()
    return totals
