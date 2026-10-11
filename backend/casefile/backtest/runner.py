"""The time-machine back-test (Phase 5). Same code path as live scoring (rule R3).

For every past renewal outcome we rebuild what was knowable on a past date with
``AccountHistory.as_of`` and score it with the same signal and scoring functions the live queue uses:

- **Horizon mode:** each renewal scored 30, 60 and 90 days before its planned date.
- **Rolling mode:** every week, all accounts with a renewal in the next 90 days are ranked; we
  measure precision@10 and, for each lost renewal, how early Casefile first flagged it.

Honesty rules built in:
- Every metric carries n and a 95% bootstrap range.
- Tuning (the flag threshold, the "best single signal" baseline) uses only the earlier half of the
  renewals; results are reported on the later, held-out half as well as on everything.
- Signals whose inputs exist only for part of the history (AI email labels) are left out of the
  whole back-test unless coverage is complete, so early periods are not penalised for missing reads.
"""

from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.backtest.metrics import (
    auc,
    block_bootstrap,
    bootstrap,
    median,
    metric,
    precision_at_k,
    precision_recall,
    wilson,
)
from casefile.backtest.outcomes import POSITIVE
from casefile.db.models import (
    Activity,
    ActivityType,
    BacktestResult,
    BacktestRun,
    Direction,
    EvidenceLabel,
    Org,
    RenewalOutcome,
    UsageDaily,
)
from casefile.scoring.points import default_scoring, score
from casefile.signals.base import ENGINE_VERSION, default_thresholds, run_signals
from casefile.timeline.snapshot import AccountHistory, Snapshot

LABEL_SIGNALS = {"negative_tone", "upgrade_interest"}  # need AI email labels
HORIZONS = (30, 60, 90)
FLAG_GRID = (25, 30, 35, 40, 45, 50, 55, 60)
ROLLING_WINDOW_DAYS = 90
LEAD_LOOKBACK_DAYS = 180


@dataclass
class BacktestConfig:
    horizons: tuple[int, ...] = HORIZONS
    min_history_days: int = 90
    step_days: int = 7
    split_date: date | None = None  # default: median renewal date of eligible events
    exclude_signals: set[str] | None = None  # default: label signals unless coverage is complete
    label: EvidenceLabel = EvidenceLabel.simulated


@dataclass
class Scored:
    risk: int
    signals: list[str]
    days_to_renewal: int | None
    health: float | None


@dataclass
class Event:
    outcome: RenewalOutcome
    positive: bool
    scores: dict[int, Scored] = field(default_factory=dict)  # by horizon
    skipped: dict[int, str] = field(default_factory=dict)


def _noon(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time(12), tz).astimezone(UTC)


def label_coverage(session: Session, org: Org) -> float:
    """Share of inbound customer emails that the AI reader has read (any prompt version)."""
    base = (
        select(func.count())
        .select_from(Activity)
        .where(
            Activity.org_id == org.id,
            Activity.type == ActivityType.email,
            Activity.direction == Direction.inbound,
            Activity.account_id.is_not(None),
            Activity.extra["auto_reply"].astext.is_distinct_from("true"),
            Activity.extra["bounce"].astext.is_distinct_from("true"),
        )
    )
    total = session.scalar(base) or 0
    read = session.scalar(base.where(Activity.extra["ai_read"].is_not(None))) or 0
    return read / total if total else 1.0


class Scorer:
    """Scores one account as of a date, caching loaded histories and results."""

    def __init__(self, session: Session, exclude: set[str]) -> None:
        self.session = session
        self.exclude = exclude
        self.thresholds = default_thresholds()
        self.scoring = default_scoring()
        self._histories: dict[uuid.UUID, AccountHistory] = {}
        self._cache: dict[tuple[uuid.UUID, datetime], Scored | None] = {}

    def history(self, account_id: uuid.UUID) -> AccountHistory:
        if account_id not in self._histories:
            self._histories[account_id] = AccountHistory.load(self.session, account_id)
        return self._histories[account_id]

    def snapshot(self, account_id: uuid.UUID, t: datetime) -> Snapshot | None:
        return self.history(account_id).as_of(t)

    def __call__(self, account_id: uuid.UUID, t: datetime) -> Scored | None:
        key = (account_id, t)
        if key not in self._cache:
            snap = self.snapshot(account_id, t)
            if snap is None:
                self._cache[key] = None
            else:
                hits = [h for h in run_signals(snap, self.thresholds) if h.type not in self.exclude]
                s = score(snap, hits, self.scoring)
                health = self.history(account_id).fields.value(account_id, "health_score", t)
                self._cache[key] = Scored(
                    s.risk,
                    sorted({h.type for h in hits}),
                    snap.days_to_renewal(),
                    float(health) if health else None,
                )
        return self._cache[key]


def _deal_open(scorer: Scorer, o: RenewalOutcome, t: datetime) -> bool:
    snap = scorer.snapshot(o.account_id, t)
    if snap is None:
        return False
    deal = next((d for d in snap.deals if d.id == o.deal_id), None)
    return deal is None or not deal.is_closed  # not created yet counts as "not decided"


def _block(rows: list[tuple[Scored, bool]], flag_at: int, *, best_signal: str | None) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    risk = [float(r.risk) for r, _ in rows]
    y = [p for _, p in rows]
    flags = [r.risk >= flag_at for r, _ in rows]
    p, r = precision_recall(flags, y)
    tp = sum(1 for f, v in zip(flags, y, strict=True) if f and v)
    pairs = list(zip(risk, y, flags, strict=True))
    out: dict[str, Any] = {
        "n": len(rows),
        "positives": sum(y),
        "prevalence": round(sum(y) / len(y), 3),
        "auc": metric(
            auc(risk, y),
            bootstrap(pairs, lambda s: auc([a for a, _, _ in s], [b for _, b, _ in s])),
            len(rows),
        ),
        "precision": metric(p, wilson(tp, sum(flags)), sum(flags)),
        "recall": metric(r, wilson(tp, sum(y)), sum(y)),
        "precision_at_10": metric(precision_at_k(risk, y, 10), None, min(10, len(rows))),
        "flag_at": flag_at,
    }
    if best_signal:
        ind = [1.0 if best_signal in s.signals else 0.0 for s, _ in rows]
        bp, br = precision_recall([v > 0 for v in ind], y)
        btp = sum(1 for v, t in zip(ind, y, strict=True) if v > 0 and t)
        out["best_single_signal"] = {
            "signal": best_signal,
            "auc": metric(
                auc(ind, y),
                bootstrap(
                    list(zip(ind, y, strict=True)), lambda s: auc([a for a, _ in s], [b for _, b in s])
                ),
                len(rows),
            ),
            "precision": metric(bp, wilson(btp, int(sum(ind))), int(sum(ind))),
            "recall": metric(br, wilson(btp, sum(y)), sum(y)),
        }
    health = [s.health for s, _ in rows]
    out["health_score"] = (
        {"auc": metric(auc([-(h or 0) for h in health], y), None, len(rows))}
        if any(h is not None for h in health)
        else "not available: the CRM has no health-score history"
    )
    # At a fixed horizon every renewal is the same distance away, so the calendar cannot rank them.
    out["renewal_date_only"] = {"auc": metric(0.5, None, len(rows)), "note": "constant at a fixed horizon"}
    return out


def _f1(rows: list[tuple[Scored, bool]], flag_at: int) -> float:
    p, r = precision_recall([s.risk >= flag_at for s, _ in rows], [y for _, y in rows])
    return 0.0 if not p or not r else 2 * p * r / (p + r)


def run_backtest(session: Session, org: Org, config: BacktestConfig | None = None) -> BacktestRun:
    cfg = config or BacktestConfig()
    tz = ZoneInfo(org.timezone)
    coverage = label_coverage(session, org)
    exclude = (
        cfg.exclude_signals
        if cfg.exclude_signals is not None
        else (LABEL_SIGNALS if coverage < 0.95 else set())
    )
    open_at = int(default_scoring()["cases"]["open_risk_at"])
    run = BacktestRun(
        org_id=org.id,
        config={
            "horizons": list(cfg.horizons),
            "min_history_days": cfg.min_history_days,
            "step_days": cfg.step_days,
            "excluded_signals": sorted(exclude),
            "label_coverage": round(coverage, 3),
        },
        status="running",
        label=cfg.label,
        engine_version=ENGINE_VERSION,
    )
    session.add(run)
    session.flush()

    first_activity = session.scalar(select(func.min(Activity.occurred_at)).where(Activity.org_id == org.id))
    starts = [
        session.scalar(select(func.min(UsageDaily.date)).where(UsageDaily.org_id == org.id)),
        first_activity.date() if first_activity else None,
    ]
    # A company with no history yet (just connected) gets an empty report, not a crash.
    data_start = min((d for d in starts if d is not None), default=datetime.now(UTC).date())
    earliest = _noon(data_start + timedelta(days=cfg.min_history_days), tz)
    scorer = Scorer(session, exclude)
    outcomes = list(
        session.scalars(
            select(RenewalOutcome)
            .where(RenewalOutcome.org_id == org.id)
            .order_by(RenewalOutcome.renewal_date)
        )
    )
    events = [Event(o, o.outcome.value in POSITIVE) for o in outcomes]
    skipped: dict[str, int] = defaultdict(int)
    if not events:
        run.metrics = {
            "label": cfg.label.value,
            "events": {"total": 0, "scored": 0, "tuning": 0, "held_out": 0},
            "headline_ok": False,
            "fallback": "no past renewals with a known outcome yet: nothing to test. Check that renewal "
            "deals are mapped, then run it again.",
        }
        run.status, run.finished_at = "done", datetime.now(UTC)
        session.flush()
        return run

    # ---------------------------------------------------------------- horizon mode
    for ev in events:
        for h in cfg.horizons:
            t = _noon(ev.outcome.renewal_date - timedelta(days=h), tz)
            reason = None
            if t < earliest:
                reason = "before the data window had enough history"
            elif not _deal_open(scorer, ev.outcome, t):
                reason = "renewal already decided by then"
            else:
                hist = scorer.history(ev.outcome.account_id)
                created = hist.account.created_in_source_at
                if created and t - created < timedelta(days=cfg.min_history_days):
                    reason = "account too new"
            scored = None if reason else scorer(ev.outcome.account_id, t)
            if scored is None:
                ev.skipped[h] = reason or "account did not exist"
                skipped[ev.skipped[h]] += 1
                continue
            ev.scores[h] = scored
            session.add(
                BacktestResult(
                    run_id=run.id,
                    account_id=ev.outcome.account_id,
                    renewal_date=ev.outcome.renewal_date,
                    horizon_days=h,
                    as_of=t,
                    score=float(scored.risk),
                    flagged=scored.risk >= open_at,
                    outcome=ev.outcome.outcome.value,
                    baseline_scores={"signals": scored.signals, "days_to_renewal": scored.days_to_renewal},
                )
            )

    eligible = [ev for ev in events if ev.scores]
    split = cfg.split_date or (
        statistics.median_low([ev.outcome.renewal_date for ev in eligible]) if eligible else date.today()
    )
    tune = [ev for ev in eligible if ev.outcome.renewal_date < split]
    held = [ev for ev in eligible if ev.outcome.renewal_date >= split]

    def rows(evs: list[Event], h: int) -> list[tuple[Scored, bool]]:
        return [(ev.scores[h], ev.positive) for ev in evs if h in ev.scores]

    # Tuning on the earlier half only: flag threshold (F1 at 60 days) and the best single signal.
    tune_rows = rows(tune, 60 if 60 in cfg.horizons else cfg.horizons[0])
    tuned_at = max(FLAG_GRID, key=lambda f: (_f1(tune_rows, f), -abs(f - open_at))) if tune_rows else open_at
    signal_names = sorted({s for r, _ in tune_rows for s in r.signals})
    best_signal = (
        max(
            signal_names,
            key=lambda s: (
                auc([1.0 if s in r.signals else 0.0 for r, _ in tune_rows], [y for _, y in tune_rows]) or 0
            ),
        )
        if signal_names
        else None
    )
    horizon: dict[str, Any] = {}
    for h in cfg.horizons:
        horizon[str(h)] = {
            "all": _block(rows(eligible, h), open_at, best_signal=best_signal),
            "tuning_period": _block(rows(tune, h), open_at, best_signal=best_signal),
            "held_out": _block(rows(held, h), open_at, best_signal=best_signal),
            "held_out_tuned_threshold": _block(rows(held, h), tuned_at, best_signal=None),
        }

    # ---------------------------------------------------------------- rolling mode
    last = max((o.renewal_date for o in outcomes), default=data_start)
    weeks: list[datetime] = []
    t = earliest
    while t.date() < last:
        weeks.append(t)
        t += timedelta(days=cfg.step_days)
    weekly_p10: list[float] = []
    weekly_calendar: list[float] = []
    weekly_signal: list[float] = []
    week_rows = 0
    for t in weeks:
        scored_rows = []
        for ev in events:
            days_left = (ev.outcome.renewal_date - t.date()).days
            if 0 < days_left <= ROLLING_WINDOW_DAYS and _deal_open(scorer, ev.outcome, t):
                s = scorer(ev.outcome.account_id, t)
                if s is not None:
                    scored_rows.append((ev, s, days_left))
        if len(scored_rows) < 10 or not any(ev.positive for ev, _, _ in scored_rows):
            continue
        scored_rows.sort(key=lambda r: r[1].risk, reverse=True)
        for rank, (ev, s, _) in enumerate(scored_rows, 1):
            session.add(
                BacktestResult(
                    run_id=run.id,
                    account_id=ev.outcome.account_id,
                    renewal_date=ev.outcome.renewal_date,
                    horizon_days=None,
                    as_of=t,
                    score=float(s.risk),
                    rank=rank,
                    flagged=s.risk >= open_at,
                    outcome=ev.outcome.outcome.value,
                    baseline_scores={"signals": s.signals, "days_to_renewal": s.days_to_renewal},
                )
            )
            week_rows += 1
        labels = [ev.positive for ev, _, _ in scored_rows]
        weekly_p10.append(precision_at_k([float(s.risk) for _, s, _ in scored_rows], labels, 10) or 0.0)
        weekly_calendar.append(precision_at_k([-float(d) for _, _, d in scored_rows], labels, 10) or 0.0)
        if best_signal:
            weekly_signal.append(
                precision_at_k(
                    [(1.0 if best_signal in s.signals else 0.0) - d / 1000 for _, s, d in scored_rows],
                    labels,
                    10,
                )
                or 0.0
            )
    rolling = {
        "weeks": len(weekly_p10),
        "rows": week_rows,
        "precision_at_10": metric(
            statistics.fmean(weekly_p10) if weekly_p10 else None,
            block_bootstrap(weekly_p10),
            len(weekly_p10),
            unit="mean over weeks; range from 13-week block bootstrap",
        ),
        "baseline_nearest_renewal_first": metric(
            statistics.fmean(weekly_calendar) if weekly_calendar else None,
            block_bootstrap(weekly_calendar),
            len(weekly_calendar),
        ),
        "baseline_best_single_signal": metric(
            statistics.fmean(weekly_signal) if weekly_signal else None,
            block_bootstrap(weekly_signal),
            len(weekly_signal),
            signal=best_signal,
        ),
    }

    # ---------------------------------------------------------------- lead time and false alarms
    lead_days: list[float] = []
    missed: list[dict[str, Any]] = []
    false_alarm: list[bool] = []
    for ev in events:
        grid = [
            ev.outcome.renewal_date - timedelta(days=d) for d in range(LEAD_LOOKBACK_DAYS, 0, -cfg.step_days)
        ]
        times = [_noon(d, tz) for d in grid if _noon(d, tz) >= earliest]
        times = [x for x in times if _deal_open(scorer, ev.outcome, x)]
        if not times:
            continue
        first = next(
            (x for x in times if (s := scorer(ev.outcome.account_id, x)) and s.risk >= open_at), None
        )
        if ev.positive:
            if first is None:
                missed.append(
                    {
                        "account_id": str(ev.outcome.account_id),
                        "renewal_date": ev.outcome.renewal_date.isoformat(),
                        "outcome": ev.outcome.outcome.value,
                    }
                )
            else:
                lead_days.append(float((ev.outcome.renewal_date - first.astimezone(tz).date()).days))
        else:
            window = [
                x
                for x in times
                if (ev.outcome.renewal_date - x.astimezone(tz).date()).days <= ROLLING_WINDOW_DAYS
            ]
            false_alarm.append(
                any((s := scorer(ev.outcome.account_id, x)) and s.risk >= open_at for x in window)
            )
    flagged_positives = len(lead_days)
    positives_seen = flagged_positives + len(missed)
    lead = {
        "positives": positives_seen,
        "flagged_before_outcome": metric(
            flagged_positives / positives_seen if positives_seen else None,
            wilson(flagged_positives, positives_seen),
            positives_seen,
        ),
        "median_lead_days": metric(
            median(lead_days), bootstrap(lead_days, lambda s: median(s)), len(lead_days)
        ),
        "share_flagged_30_days_ahead": metric(
            sum(1 for d in lead_days if d >= 30) / positives_seen if positives_seen else None,
            None,
            positives_seen,
        ),
        "missed": missed,
    }
    alarms = {
        "negatives": len(false_alarm),
        "flagged_in_last_90_days": metric(
            sum(false_alarm) / len(false_alarm) if false_alarm else None,
            wilson(sum(false_alarm), len(false_alarm)),
            len(false_alarm),
        ),
    }

    held_pos = sum(ev.positive for ev in held)
    run.metrics = {
        "label": cfg.label.value,
        "outcomes": {
            k: sum(1 for o in outcomes if o.outcome.value == k)
            for k in sorted({o.outcome.value for o in outcomes})
        },
        "positive_definition": sorted(POSITIVE),
        "events": {"total": len(events), "scored": len(eligible), "tuning": len(tune), "held_out": len(held)},
        "split_date": split.isoformat(),
        "skipped": dict(skipped),
        "flag_threshold": {
            "default": open_at,
            "tuned_on_tuning_period": tuned_at,
            "grid": list(FLAG_GRID),
            "objective": "F1 at 60 days",
        },
        "best_single_signal": {"name": best_signal, "chosen_on": "tuning period, AUC at 60 days"},
        "horizon": horizon,
        "rolling": rolling,
        "lead_time": lead,
        "false_alarms": alarms,
        "excluded_signals": sorted(exclude),
        "label_coverage": round(coverage, 3),
        "headline_ok": held_pos >= 10,
        "fallback": None
        if held_pos >= 10
        else "fewer than 10 lost renewals held out: use case-by-case replays, not a headline number",
    }
    run.status, run.finished_at = "done", datetime.now(UTC)
    session.flush()
    return run
