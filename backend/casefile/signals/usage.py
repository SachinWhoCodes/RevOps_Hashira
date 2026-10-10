"""Product-usage signals: decline (risk), seat saturation and new-team adoption (opportunity)."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

from casefile.db.models import Polarity
from casefile.signals.base import Evidence, SignalHit, day_label, signal
from casefile.timeline.snapshot import Snapshot, UsageDay


def _window(snap: Snapshot, start_days_ago: int, end_days_ago: int) -> list[UsageDay]:
    """Usage rows for local days in [today - start_days_ago, today - end_days_ago)."""
    lo = snap.today - timedelta(days=start_days_ago)
    hi = snap.today - timedelta(days=end_days_ago)
    return [u for u in snap.usage if lo <= u.date < hi]


def _pct_change(new: float, old: float) -> float:
    return (new - old) / old * 100 if old else 0.0


@signal(
    "usage_decline", Polarity.risk, "usage", "Active users or key actions down 25%+ (28 days vs 28 before)"
)
def usage_decline(snap: Snapshot, cfg: dict[str, Any]) -> list[SignalHit]:
    w, b = cfg["window_days"], cfg["baseline_days"]
    recent, earlier = _window(snap, w, 0), _window(snap, 2 * w, w)
    baseline = _window(snap, w + b, w)
    if len(recent) < cfg["min_days_covered"] or len(earlier) < cfg["min_days_covered"]:
        return []  # not enough data to compare fairly

    def avg(rows: list[UsageDay], attr: str) -> float:
        return sum(getattr(u, attr) for u in rows) / len(rows)

    users_new, users_old = avg(recent, "active_users"), avg(earlier, "active_users")
    if users_old < cfg["min_baseline_avg"]:
        return []  # tiny accounts swing wildly; a drop from 2 to 1 user is not a signal
    actions_new, actions_old = avg(recent, "key_actions"), avg(earlier, "key_actions")
    users_change = round(_pct_change(users_new, users_old))
    actions_change = round(_pct_change(actions_new, actions_old))
    sudden = -min(users_change, actions_change)
    # Slow slides look flat month to month; compare with the quarter before as well.
    has_baseline = len(baseline) >= b // 2
    users_base = avg(baseline, "active_users") if has_baseline else None
    base_change = round(_pct_change(users_new, users_base)) if users_base else None
    slow = -base_change if base_change is not None else 0
    if sudden < cfg["min_drop_pct"] and slow < cfg["min_drop_pct_vs_baseline"]:
        return []
    r0, r1, e0 = recent[0].date, recent[-1].date, earlier[0].date
    if sudden >= cfg["min_drop_pct"]:
        # Quote the numbers of the metric we name, never another metric's.
        if users_change <= actions_change:
            metric, before, after = "Active users", f"{users_old:.0f}", f"{users_new:.0f}"
            unit = "average daily active users"
        else:
            metric, before, after = "Key actions", f"{actions_old:.0f}", f"{actions_new:.0f}"
            unit = "average key actions a day"
        summary = (
            f"{metric} fell {sudden}% over the last {w} days compared with the {w} days before "
            f"({unit} {before} → {after})."
        )
        kind, drop, start = "sudden", sudden, e0
    else:
        assert users_base is not None
        summary = (
            f"Active users fell {slow}% over the last {w} days compared with the {b} days before them "
            f"(average daily active users {users_base:.0f} → {users_new:.0f}), a slow decline."
        )
        kind, drop, start = "gradual", slow, baseline[0].date
    return [
        SignalHit(
            type="usage_decline",
            polarity=Polarity.risk,
            value=drop,
            summary=summary,
            facts={
                "decline_kind": kind,
                "active_users_change_pct": users_change,
                "key_actions_change_pct": actions_change,
                "active_users_change_vs_baseline_pct": base_change,
                "active_users_avg_recent": round(users_new, 1),
                "active_users_avg_earlier": round(users_old, 1),
                "active_users_avg_baseline": round(users_base, 1) if users_base else None,
                "key_actions_avg_recent": round(actions_new, 1),
                "key_actions_avg_earlier": round(actions_old, 1),
                "window_days": w,
                "baseline_days": b,
                "recent_window": f"{r0.isoformat()}..{r1.isoformat()}",
            },
            evidence=(
                Evidence(
                    "usage",
                    f"usage:{start.isoformat()}:{r1.isoformat()}",
                    f"Daily usage {day_label(start)} to {day_label(r1)}",
                    r1.isoformat(),
                ),
            ),
        )
    ]


@signal("seat_saturation", Polarity.opportunity, "usage+crm", "90%+ of purchased seats in use")
def seat_saturation(snap: Snapshot, cfg: dict[str, Any]) -> list[SignalHit]:
    seats = snap.account.seats_purchased
    recent = [u for u in _window(snap, cfg["recent_days"], 0) if u.seats_used is not None]
    if not seats or not recent:
        return []
    used = round(sum(u.seats_used or 0 for u in recent) / len(recent))
    ratio = used / seats
    if ratio < cfg["min_ratio"]:
        return []
    pct = round(ratio * 100)
    last = recent[-1].date
    return [
        SignalHit(
            type="seat_saturation",
            polarity=Polarity.opportunity,
            value=pct,
            summary=(
                f"{used} of {seats} purchased seats are in use "
                f"({pct}%, average over the last {cfg['recent_days']} days)."
            ),
            facts={
                "seats_used": used,
                "seats_purchased": seats,
                "seat_utilisation_pct": pct,
                "window_days": cfg["recent_days"],
            },
            evidence=(
                Evidence(
                    "usage",
                    f"usage:{recent[0].date.isoformat()}:{last.isoformat()}",
                    f"Seats in use, week to {day_label(last)}",
                    last.isoformat(),
                ),
                Evidence("field", f"{snap.account.id}:seats_purchased", f"Seats purchased: {seats}", None),
            ),
        )
    ]


@signal(
    "new_team_adoption", Polarity.opportunity, "usage", "Active users from a department new to the product"
)
def new_team_adoption(snap: Snapshot, cfg: dict[str, Any]) -> list[SignalHit]:
    recent_days, baseline_days = cfg["recent_days"], cfg["baseline_days"]
    recent = _window(snap, recent_days, 0)
    baseline = _window(snap, recent_days + baseline_days, recent_days)
    if len(baseline) < baseline_days // 2 or not recent:
        return []  # without a baseline we can't tell what is "new"
    seen_before = {dept for u in baseline for dept, n in u.departments.items() if n}
    totals: dict[str, int] = defaultdict(int)
    first_day: dict[str, Any] = {}
    for u in recent:
        for dept, n in u.departments.items():
            if n and dept not in seen_before:
                totals[dept] += int(n)
                first_day.setdefault(dept, u.date)
    hits = []
    for dept, total in sorted(totals.items()):
        avg = total / len(recent)
        if avg < cfg["min_avg_users"]:
            continue
        since = first_day[dept]
        hits.append(
            SignalHit(
                type="new_team_adoption",
                polarity=Polarity.opportunity,
                value=round(avg, 1),
                summary=(
                    f"A new team is using the product: {dept} "
                    f"(about {avg:.1f} active users a day since {day_label(since)})."
                ),
                facts={
                    "department": dept,
                    "avg_daily_users": round(avg, 1),
                    "first_active_date": since.isoformat(),
                },
                evidence=(
                    Evidence(
                        "usage",
                        f"usage:{since.isoformat()}:{recent[-1].date.isoformat()}",
                        f"{dept} usage since {day_label(since)}",
                        since.isoformat(),
                    ),
                ),
            )
        )
    return hits
