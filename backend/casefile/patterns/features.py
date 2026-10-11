"""What a renewal cycle looks like at one moment: a few numbers computed from an as-of snapshot.

Pure functions of a ``Snapshot`` and its signal hits (rule R3): the live page, the training and the
held-out test all describe a moment the same way, and nothing after ``as_of`` can leak in.

The numbers are relative to the client itself where size would otherwise dominate (usage compared
with the client's own earlier usage, seats as a share of what they bought), so a 3-person customer
and a 300-person one with the same shape of decline look alike.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from casefile.signals.base import SignalHit
from casefile.timeline.snapshot import Snapshot, UsageDay

MAX_DAYS = 180.0  # "days since" values are capped: a year without a meeting is no worse than half a year


@dataclass(frozen=True)
class FeatureSpec:
    key: str
    label: str  # plain words for the page
    unit: str  # score | pct | share | days | count | flag
    group: str  # crm | usage | engagement
    log: bool = False  # compare on a log scale (counts that vary a lot between clients)


FEATURES: tuple[FeatureSpec, ...] = (
    FeatureSpec("risk", "Rules risk score", "score", "crm"),
    FeatureSpec("usage.users_vs_6mo", "Active users vs 6 months earlier", "pct", "usage"),
    FeatureSpec("usage.users_vs_prior", "Active users vs the 3 months before", "pct", "usage"),
    FeatureSpec("usage.seat_use", "Seats in use (of purchased)", "share", "usage"),
    FeatureSpec("eng.days_since_meeting", "Days since the last meeting", "days", "engagement"),
    FeatureSpec("eng.meetings_90d", "Meetings in the last 90 days", "count", "engagement"),
    FeatureSpec(
        "eng.customer_emails_60d", "Emails from the customer in 60 days", "count", "engagement", log=True
    ),
    FeatureSpec("crm.next_step_missing", "Renewal next step missing", "flag", "crm"),
    FeatureSpec("eng.buyer_met_60d", "Economic buyer met in 60 days", "flag", "engagement"),
)
SPEC = {f.key: f for f in FEATURES}


def _avg(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _window(
    usage: tuple[UsageDay, ...], today: date, start_days_ago: int, end_days_ago: int
) -> list[UsageDay]:
    lo, hi = today - timedelta(days=start_days_ago), today - timedelta(days=end_days_ago)
    return [u for u in usage if lo <= u.date < hi]


def _change(new: float | None, old: float | None) -> float | None:
    if new is None or not old:
        return None
    return round(max(-1.0, min(1.0, (new - old) / old)), 4)


def describe(snap: Snapshot, hits: list[SignalHit], risk: int) -> dict[str, float | None]:
    """The pattern of one moment. ``None`` means "no data for this" (e.g. no product usage)."""
    types = {h.type for h in hits}
    today = snap.today
    out: dict[str, float | None] = {"risk": float(risk)}

    recent = _window(snap.usage, today, 28, 0)
    prior = _window(snap.usage, today, 118, 28)
    earlier = _window(snap.usage, today, 208, 180)
    users_now = _avg([u.active_users for u in recent]) if len(recent) >= 20 else None
    out["usage.users_vs_6mo"] = (
        _change(users_now, _avg([u.active_users for u in earlier])) if len(earlier) >= 20 else None
    )
    out["usage.users_vs_prior"] = (
        _change(users_now, _avg([u.active_users for u in prior])) if len(prior) >= 60 else None
    )
    seats = snap.account.seats_purchased
    used = [float(u.seats_used) for u in recent if u.seats_used is not None]
    out["usage.seat_use"] = (
        round(min(1.5, statistics.fmean(used) / seats), 4) if seats and len(used) >= 20 else None
    )

    held = [
        a for a in snap.activities if a.type == "meeting" and not a.cancelled and a.occurred_at <= snap.as_of
    ]
    last = max((a.occurred_at for a in held), default=None)
    out["eng.days_since_meeting"] = min(MAX_DAYS, float((snap.as_of - last).days)) if last else MAX_DAYS
    out["eng.meetings_90d"] = float(sum(1 for a in held if a.occurred_at >= snap.as_of - timedelta(days=90)))
    out["eng.customer_emails_60d"] = float(
        sum(
            1
            for a in snap.activities
            if a.type == "email"
            and a.direction == "inbound"
            and not a.extra.get("auto_reply")
            and not a.extra.get("bounce")
            and snap.as_of - timedelta(days=60) <= a.occurred_at <= snap.as_of
        )
    )
    out["crm.next_step_missing"] = 1.0 if "missing_next_step" in types else 0.0
    out["eng.buyer_met_60d"] = 1.0 if "executive_engagement" in types else 0.0
    return out


def scaled(key: str, value: float | None) -> float | None:
    """The value as compared: counts that vary a lot between clients go on a log scale."""
    if value is None:
        return None
    return math.log1p(value) if SPEC[key].log else value
