"""Train an organisation's renewal-pattern library and match every current client against it.

1. **Cut history into cycles.** A cycle ends at a renewal decision (renewed, late, downgraded or
   churned — labels derived from CRM deal history, ``casefile.backtest.outcomes``) and starts at the
   previous one.
2. **Describe each finished cycle** at 30, 60 … 360 days before its decision, through the same as-of
   snapshots, signals and scoring as the live queue and the back-test (rule R3).
3. **Learn** per stage which numbers told renewals and losses apart in this company's history.
4. **Test** the matching on the later half of the history with no look-ahead, against the rules.
5. **Match** each current client's open cycle (its path to today) with the finished cycles at the
   same distance from their decision.

What is stored is the patterns (a few numbers per checkpoint), the learned weights and the matches;
nothing is sent anywhere and no AI runs. A company with no finished renewals gets a model in
``no_history`` state that still places every client in its current cycle.
"""

from __future__ import annotations

import time as clock
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from casefile.backtest.outcomes import POSITIVE, derive_outcomes
from casefile.backtest.runner import LABEL_SIGNALS, Scorer, _deal_open, label_coverage
from casefile.db.models import (
    Account,
    Activity,
    CyclePattern,
    Deal,
    Org,
    PatternMatch,
    PatternModel,
    RenewalOutcome,
    UsageDaily,
)
from casefile.patterns.features import FEATURES, SPEC, describe
from casefile.patterns.matching import (
    PATH_STEPS,
    STAGES,
    STEP_DAYS,
    Cycle,
    Features,
    K,
    StageModel,
    evaluate,
    learn_stage,
    nearest,
    outcome_mix,
)
from casefile.scoring.points import score
from casefile.signals.base import run_signals

ENGINE_VERSION = "patterns-1"
MIN_HISTORY_DAYS = 90  # a moment needs this much data before it, or "no meetings" just means "no data yet"
MIN_CYCLES = 20  # finished cycles before the library is "ready"
MIN_LOST = 5  # ...of which at least this many lost revenue
KEEP_MODELS = 3  # older trainings are pruned
TOP_DIFFERENCES = 5


@dataclass
class _Moment:
    at: datetime
    features: Features


class _Describer:
    """Describes an account at a moment, with the same signals and scoring the back-test uses."""

    def __init__(self, session: Session, exclude: set[str]) -> None:
        self.scorer = Scorer(session, exclude)
        self.exclude = exclude

    def __call__(self, account_id: uuid.UUID, t: datetime) -> Features | None:
        snap = self.scorer.snapshot(account_id, t)
        if snap is None:
            return None
        hits = [h for h in run_signals(snap, self.scorer.thresholds) if h.type not in self.exclude]
        return describe(snap, hits, score(snap, hits, self.scorer.scoring).risk)


def _noon(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time(12), tz).astimezone(UTC)


def stage_for(days_to_renewal: int) -> int:
    """The checkpoint a client is at: the nearest stage, kept within 30 to 360 days."""
    nearest_stage = STEP_DAYS * round(days_to_renewal / STEP_DAYS)
    return max(STAGES[0], min(STAGES[-1], nearest_stage))


def data_window_start(session: Session, org: Org) -> date | None:
    first_activity = session.scalar(select(func.min(Activity.occurred_at)).where(Activity.org_id == org.id))
    starts = [
        session.scalar(select(func.min(UsageDaily.date)).where(UsageDaily.org_id == org.id)),
        first_activity.date() if first_activity else None,
    ]
    return min((d for d in starts if d is not None), default=None)


def _status(cycles: list[Cycle], stages: dict[int, StageModel]) -> str:
    if not cycles:
        return "no_history"
    lost = sum(1 for c in cycles if c.lost)
    if len(cycles) < MIN_CYCLES or lost < MIN_LOST or not any(m.ready for m in stages.values()):
        return "learning"
    return "ready"


def _differences(model: StageModel, now: Features) -> list[dict[str, Any]]:
    ranked = sorted(((w, k) for k, w in model.weights.items() if w > 0), reverse=True)[:TOP_DIFFERENCES]
    return [
        {
            "feature": key,
            "label": SPEC[key].label,
            "unit": SPEC[key].unit,
            "weight": round(w, 3),
            "client": now.get(key),
            "kept": model.means.get("kept", {}).get(key),
            "lost": model.means.get("lost", {}).get(key),
        }
        for w, key in ranked
    ]


def train(session: Session, org: Org, *, now: datetime | None = None) -> PatternModel:
    """Train, test and match for one organisation; returns the stored model (caller commits)."""
    from casefile.onboarding import data_label

    started = clock.monotonic()
    now = now or datetime.now(UTC)
    tz = ZoneInfo(org.timezone)
    derive_outcomes(session, org)  # cycle endings from CRM renewal history (idempotent)
    coverage = label_coverage(session, org)
    # Like the back-test: signals that need AI email labels are left out unless the past was read.
    exclude = set(LABEL_SIGNALS) if coverage < 0.95 else set()
    moment = _Describer(session, exclude)
    window_start = data_window_start(session, org)
    earliest = _noon(window_start + timedelta(days=MIN_HISTORY_DAYS), tz) if window_start else now

    outcomes = list(
        session.scalars(
            select(RenewalOutcome)
            .where(RenewalOutcome.org_id == org.id)
            .order_by(RenewalOutcome.account_id, RenewalOutcome.renewal_date)
        )
    )
    previous: dict[uuid.UUID, date | None] = {}
    finished: list[tuple[RenewalOutcome, date | None, dict[int, _Moment]]] = []
    last_decision: dict[uuid.UUID, date] = {}
    undescribed: dict[str, int] = defaultdict(int)  # why a finished cycle has no pattern at all
    for o in outcomes:
        start = previous.get(o.account_id)
        previous[o.account_id] = o.renewal_date
        if o.renewal_date <= now.astimezone(tz).date():
            last_decision[o.account_id] = o.renewal_date
        created = moment.scorer.history(o.account_id).account.created_in_source_at
        points: dict[int, _Moment] = {}
        first_reason: str | None = None
        for stage in STAGES:
            t = _noon(o.renewal_date - timedelta(days=stage), tz)
            reason = None
            if t > now:
                reason = "decision still ahead"
            elif t < earliest:
                reason = "before the data had 90 days of history"
            elif start is not None and t.date() <= start:
                reason = "cycle shorter than the checkpoint"
            elif created and t - created < timedelta(days=MIN_HISTORY_DAYS):
                reason = "the CRM record is newer than 90 days at that point"
            elif not _deal_open(moment.scorer, o, t):
                reason = "decided before the checkpoint"  # the cycle had already ended
            f = None if reason else moment(o.account_id, t)
            if f is None:
                first_reason = first_reason or reason or "no record of the client yet"
                continue
            points[stage] = _Moment(t, f)
        if not points:
            undescribed[first_reason or "no checkpoint"] += 1
        finished.append((o, start, points))

    version = (
        session.scalar(select(func.max(PatternModel.version)).where(PatternModel.org_id == org.id)) or 0
    ) + 1
    model = PatternModel(
        org_id=org.id,
        version=version,
        status="running",
        label=data_label(session, org),
        engine_version=ENGINE_VERSION,
        trained_at=now,
    )
    session.add(model)
    session.flush()

    rows: list[CyclePattern] = []
    for o, start, points in finished:
        if not points:
            continue
        row = CyclePattern(
            org_id=org.id,
            model_id=model.id,
            account_id=o.account_id,
            outcome_id=o.id,
            cycle_start=start,
            cycle_end=o.renewal_date,
            decided_at=o.decided_at,
            outcome=o.outcome.value,
            arr_before=o.arr_before,
            arr_after=o.arr_after,
            points={str(s): {"at": m.at.isoformat(), "f": m.features} for s, m in points.items()},
        )
        session.add(row)
        rows.append(row)
    session.flush()
    cycles = [
        Cycle(
            key=str(r.id),
            end=r.cycle_end,
            lost=r.outcome in POSITIVE,
            points={int(s): (datetime.fromisoformat(p["at"]), p["f"]) for s, p in r.points.items()},
            known=r.decided_at.astimezone(tz).date() if r.decided_at else None,
        )
        for r in rows
    ]
    stages = {s: learn_stage(s, [(c.points[s][1], c.lost) for c in cycles if s in c.points]) for s in STAGES}

    # ------------------------------------------------------------ current clients
    customers = session.scalars(
        select(Account)
        .where(Account.org_id == org.id, exists().where(Deal.account_id == Account.id))
        .order_by(Account.name)
    ).all()
    today = now.astimezone(tz).date()
    matched = 0
    for acc in customers:
        snap = moment.scorer.snapshot(acc.id, now)
        if snap is None or snap.is_churned():
            continue
        days = snap.days_to_renewal()
        detail: dict[str, Any] = {"cycle_start": None, "trajectory": [], "now": None, "note": None}
        start = last_decision.get(acc.id)
        detail["cycle_start"] = start.isoformat() if start else None
        if days is None:
            detail["note"] = "No renewal date in the CRM, so this client can't be placed in a cycle."
            session.add(
                PatternMatch(
                    org_id=org.id, model_id=model.id, account_id=acc.id, computed_at=now, detail=detail
                )
            )
            continue
        hits = [h for h in run_signals(snap, moment.scorer.thresholds) if h.type not in exclude]
        now_features = describe(snap, hits, score(snap, hits, moment.scorer.scoring).risk)
        stage = stage_for(days)
        renewal = snap.account.renewal_date
        assert renewal is not None
        trajectory: dict[int, Features] = {}
        created = moment.scorer.history(acc.id).account.created_in_source_at
        for s in STAGES:
            t = _noon(renewal - timedelta(days=s), tz)
            if s <= days or t > now or t < earliest or (start is not None and t.date() <= start):
                continue
            if created and t - created < timedelta(days=MIN_HISTORY_DAYS):
                continue
            f = moment(acc.id, t)
            if f is not None:
                trajectory[s] = f
                detail["trajectory"].append({"days": s, "at": t.isoformat(), "f": f})
        detail["now"] = {"days": days, "at": now.isoformat(), "f": now_features}
        if days < 0:
            detail["note"] = (
                "The renewal date has passed and the deal is still open: the decision is pending."
            )
        elif days > STAGES[-1] + STEP_DAYS // 2:
            detail["note"] = (
                "More than a year to the renewal: compared with cycles a year before their decision."
            )
        path = {
            stage: now_features,
            **{
                s: trajectory[s]
                for s in range(stage + STEP_DAYS, stage + STEP_DAYS * PATH_STEPS, STEP_DAYS)
                if s in trajectory
            },
        }
        stage_model = stages[stage]
        verdict_word: str | None = None
        lost = k = None
        if stage_model.ready:
            near, compared = nearest(path, [c for c in cycles if c.known_on < today], stage_model)
            mix = outcome_mix(near, stage_model.base_rate)
            detail["neighbours"] = [
                {"cycle_id": n.key, "distance": n.distance, "closer_than": n.closer_than, "lost": n.lost}
                for n in near
            ]
            detail["mix"] = mix
            detail["compared"] = compared
            detail["differences"] = _differences(stage_model, now_features)
            verdict_word, lost, k = mix["verdict"], mix["lost"], mix["k"]
            matched += 1
        else:
            detail["note"] = detail["note"] or (
                f"Only {stage_model.n} finished cycles ({stage_model.lost} lost) were observed "
                f"{stage} days before their decision: too few to compare yet."
            )
        session.add(
            PatternMatch(
                org_id=org.id,
                model_id=model.id,
                account_id=acc.id,
                computed_at=now,
                renewal_date=renewal,
                days_to_renewal=days,
                stage=stage,
                verdict=verdict_word,
                lost=lost,
                k=k,
                detail=detail,
            )
        )

    counts: dict[str, int] = defaultdict(int)
    for o, _, _ in finished:
        counts[o.outcome.value] += 1
    lost_cycles = sum(1 for c in cycles if c.lost)
    model.status = _status(cycles, stages)
    model.params = {
        "k": K,
        "path_steps": PATH_STEPS,
        "stages": {str(s): m.to_dict() for s, m in stages.items()},
        "features": [{"key": f.key, "label": f.label, "unit": f.unit, "group": f.group} for f in FEATURES],
        "excluded_signals": sorted(exclude),
        "label_coverage": round(coverage, 3),
        "min_cycles": MIN_CYCLES,
        "min_lost": MIN_LOST,
    }
    model.evaluation = evaluate(cycles) if model.status == "ready" else {"stages": {}, "split": None}
    model.stats = {
        "outcomes": dict(counts),
        "finished_cycles": len(finished),
        "described_cycles": len(cycles),
        "undescribed": dict(undescribed),
        "lost_cycles": lost_cycles,
        "kept_cycles": len(cycles) - lost_cycles,
        "patterns": sum(len(c.points) for c in cycles),
        "clients": len(customers),
        "matched_clients": matched,
        "data_start": window_start.isoformat() if window_start else None,
        "seconds": round(clock.monotonic() - started, 2),
    }
    session.execute(
        delete(PatternModel).where(
            PatternModel.org_id == org.id, PatternModel.version <= version - KEEP_MODELS
        )
    )
    session.flush()
    return model


def latest_model(session: Session, org_id: uuid.UUID) -> PatternModel | None:
    return session.scalar(
        select(PatternModel)
        .where(PatternModel.org_id == org_id, PatternModel.status != "running")
        .order_by(PatternModel.version.desc())
        .limit(1)
    )
