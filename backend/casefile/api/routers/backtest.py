"""Back-test results, outcome label review, and per-account replay ("what would Casefile have said?")."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.ai.case_writer import template_explanation
from casefile.ai.evidence import build_pack
from casefile.api.deps import current_org, current_user, get_db, require_admin
from casefile.backtest.outcomes import POSITIVE, correct_label, derive_outcomes
from casefile.backtest.runner import LABEL_SIGNALS, BacktestConfig, Scorer, label_coverage, run_backtest
from casefile.db.models import (
    Account,
    AppUser,
    BacktestResult,
    BacktestRun,
    Org,
    RenewalOutcome,
    RenewalOutcomeKind,
)
from casefile.scoring.points import default_scoring, score
from casefile.signals.base import default_thresholds, run_signals

router = APIRouter(tags=["backtest"])


class LabelIn(BaseModel):
    outcome: RenewalOutcomeKind
    note: str | None = None


def _latest(db: Session, org: Org) -> BacktestRun | None:
    return db.scalar(
        select(BacktestRun)
        .where(BacktestRun.org_id == org.id, BacktestRun.status == "done")
        .order_by(BacktestRun.started_at.desc())
        .limit(1)
    )


def _run_view(run: BacktestRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "label": run.label.value,
        "engine_version": run.engine_version,
        "config": run.config,
        "metrics": run.metrics,
    }


@router.get("/backtest/latest")
def latest(db: Session = Depends(get_db), org: Org = Depends(current_org)) -> dict[str, Any] | None:
    run = _latest(db, org)
    return _run_view(run) if run else None


@router.post("/backtest/run")
def run_now(
    db: Session = Depends(get_db), org: Org = Depends(current_org), _: AppUser = Depends(require_admin)
) -> dict[str, Any]:
    """Re-derive labels and run the back-test (about 15 seconds on 200 accounts)."""
    derive_outcomes(db, org)
    run = run_backtest(db, org, BacktestConfig())
    db.commit()
    return _run_view(run)


@router.get("/backtest/outcomes")
def outcomes(db: Session = Depends(get_db), org: Org = Depends(current_org)) -> list[dict[str, Any]]:
    run = _latest(db, org)
    names = dict(db.execute(select(Account.id, Account.name).where(Account.org_id == org.id)).all())
    scores: dict[tuple[uuid.UUID, date], dict[str, float]] = {}
    if run:
        for r in db.scalars(
            select(BacktestResult).where(
                BacktestResult.run_id == run.id, BacktestResult.horizon_days.is_not(None)
            )
        ):
            assert r.renewal_date is not None
            scores.setdefault((r.account_id, r.renewal_date), {})[str(r.horizon_days)] = r.score
    missed = {
        (m["account_id"], m["renewal_date"])
        for m in (run.metrics.get("lead_time", {}).get("missed", []) if run else [])
    }
    rows = db.scalars(
        select(RenewalOutcome)
        .where(RenewalOutcome.org_id == org.id)
        .order_by(RenewalOutcome.renewal_date.desc())
    )
    return [
        {
            "id": str(o.id),
            "account": {"id": str(o.account_id), "name": names.get(o.account_id, "?")},
            "renewal_date": o.renewal_date.isoformat(),
            "outcome": o.outcome.value,
            "positive": o.outcome.value in POSITIVE,
            "label_source": o.label_source,
            "arr_before": float(o.arr_before) if o.arr_before is not None else None,
            "arr_after": float(o.arr_after) if o.arr_after is not None else None,
            "risk_before": scores.get((o.account_id, o.renewal_date), {}),
            "missed": (str(o.account_id), o.renewal_date.isoformat()) in missed,
        }
        for o in rows
    ]


@router.patch("/backtest/outcomes/{outcome_id}")
def relabel(
    outcome_id: uuid.UUID,
    body: LabelIn,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> dict[str, Any]:
    row = db.get(RenewalOutcome, outcome_id)
    if row is None or row.org_id != user.org_id:
        raise HTTPException(404, "outcome not found")
    correct_label(db, outcome_id, body.outcome, user_id=user.id, note=body.note)
    db.commit()
    return {"id": str(row.id), "outcome": row.outcome.value, "label_source": row.label_source}


def _exclude(db: Session, org: Org) -> set[str]:
    run = _latest(db, org)
    if run is not None:
        return set(run.config.get("excluded_signals", []))
    return LABEL_SIGNALS if label_coverage(db, org) < 0.95 else set()


@router.get("/backtest/replay/{account_id}")
def replay(
    account_id: uuid.UUID,
    weeks: int = 78,
    db: Session = Depends(get_db),
    org: Org = Depends(current_org),
    _: AppUser = Depends(current_user),
) -> dict[str, Any]:
    account = db.get(Account, account_id)
    if account is None or account.org_id != org.id:
        raise HTTPException(404, "account not found")
    tz = ZoneInfo(org.timezone)
    exclude = _exclude(db, org)
    scorer = Scorer(db, exclude)
    today = datetime.now(UTC).astimezone(tz).date()
    open_at = int(default_scoring()["cases"]["open_risk_at"])
    points = []
    for w in range(min(weeks, 156), -1, -1):
        d = today - timedelta(days=7 * w)
        t = datetime.combine(d, time(12), tz).astimezone(UTC)
        s = scorer(account_id, t)
        if s is None:
            continue
        snap = scorer.snapshot(account_id, t)
        assert snap is not None
        opp = score(snap, [h for h in run_signals(snap) if h.type not in exclude]).opportunity
        points.append(
            {
                "date": d.isoformat(),
                "risk": s.risk,
                "opportunity": opp,
                "flagged": s.risk >= open_at,
                "signals": s.signals,
            }
        )
    outs = db.scalars(select(RenewalOutcome).where(RenewalOutcome.account_id == account_id))
    return {
        "account": {"id": str(account.id), "name": account.name},
        "flag_at": open_at,
        "excluded_signals": sorted(exclude),
        "points": points,
        "outcomes": [
            {
                "renewal_date": o.renewal_date.isoformat(),
                "outcome": o.outcome.value,
                "label_source": o.label_source,
            }
            for o in outs
        ],
    }


@router.get("/backtest/replay/{account_id}/explain")
def replay_explain(
    account_id: uuid.UUID,
    as_of: date,
    db: Session = Depends(get_db),
    org: Org = Depends(current_org),
    _: AppUser = Depends(current_user),
) -> dict[str, Any]:
    """What Casefile would have said on that date: the checked template explanation (no AI, no cost)."""
    account = db.get(Account, account_id)
    if account is None or account.org_id != org.id:
        raise HTTPException(404, "account not found")
    tz = ZoneInfo(org.timezone)
    t = datetime.combine(as_of, time(12), tz).astimezone(UTC)
    scorer = Scorer(db, _exclude(db, org))
    snap = scorer.snapshot(account_id, t)
    if snap is None:
        raise HTTPException(404, "the account did not exist on that date")
    hits = [h for h in run_signals(snap, default_thresholds()) if h.type not in scorer.exclude]
    s = score(snap, hits)
    pack = build_pack(snap, hits, owner_name=None, risk=s.risk, opportunity=s.opportunity)
    written = template_explanation(hits, pack, risk=s.risk, opportunity=s.opportunity, reason="replay")
    return {
        "as_of": as_of.isoformat(),
        "risk": s.risk,
        "opportunity": s.opportunity,
        "headline": written.headline,
        "claims": written.claims,
        "signals": [h.to_dict() for h in hits],
        "source": "template (checked); AI explanations are not generated for past dates by default",
    }
