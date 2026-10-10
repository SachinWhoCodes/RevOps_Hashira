"""CRM signals: renewal timing and deal hygiene."""

from __future__ import annotations

from typing import Any

from casefile.db.models import Polarity
from casefile.signals.base import Evidence, SignalHit, day_label, iso, signal
from casefile.timeline.snapshot import Snapshot


@signal("renewal_proximity", Polarity.urgency, "crm", "Renewal within 90 days (raises urgency, not risk)")
def renewal_proximity(snap: Snapshot, cfg: dict[str, Any]) -> list[SignalHit]:
    days = snap.days_to_renewal()
    renewal = snap.account.renewal_date
    if days is None or renewal is None or days > cfg["window_days"]:
        return []
    deal = snap.renewal_deal()
    overdue = days < 0
    if overdue and deal is None:
        return []  # past date and nothing open: not a pending renewal
    summary = (
        f"The renewal date ({day_label(renewal)}) passed {-days} days ago and the renewal is still open."
        if overdue
        else f"Renewal is in {days} days ({day_label(renewal)})."
    )
    evidence = (
        Evidence(
            "field",
            f"{deal.id}:close_date",
            f"Renewal deal close date: {day_label(renewal)}",
            renewal.isoformat(),
        )
        if deal
        else Evidence(
            "field",
            f"{snap.account.id}:renewal_date",
            f"Contract renewal date: {day_label(renewal)}",
            renewal.isoformat(),
        )
    )
    return [
        SignalHit(
            type="renewal_proximity",
            polarity=Polarity.urgency,
            value=days,
            summary=summary,
            facts={"days_to_renewal": days, "renewal_date": renewal.isoformat(), "overdue": overdue},
            evidence=(evidence,),
        )
    ]


@signal("missing_next_step", Polarity.risk, "crm", "Renewal deal has no current next step")
def missing_next_step(snap: Snapshot, cfg: dict[str, Any]) -> list[SignalHit]:
    days = snap.days_to_renewal()
    deal = snap.renewal_deal()
    if deal is None:
        if days is not None and 0 <= days <= cfg["no_deal_window_days"]:
            renewal = snap.account.renewal_date
            assert renewal is not None
            return [
                SignalHit(
                    type="missing_next_step",
                    polarity=Polarity.risk,
                    value=days,
                    summary=f"Renewal is in {days} days but no renewal deal is open in the CRM.",
                    facts={"reason": "no_renewal_deal", "days_to_renewal": days},
                    evidence=(
                        Evidence(
                            "field",
                            f"{snap.account.id}:renewal_date",
                            f"Contract renewal date: {day_label(renewal)}",
                            renewal.isoformat(),
                        ),
                    ),
                )
            ]
        return []
    if deal.close_date is None or (deal.close_date - snap.today).days > cfg["renewal_window_days"]:
        return []
    set_on = snap.local_date(deal.next_step_since) if deal.next_step_since else None
    age = (snap.today - set_on).days if set_on else None
    if not deal.next_step:
        reason = "empty"
        summary = (
            f"The renewal deal has no next step recorded (cleared on {day_label(set_on)})."
            if deal.next_step_ever_set and set_on
            else "The renewal deal has never had a next step recorded."
        )
    elif deal.next_step_date and deal.next_step_date < snap.today:
        reason = "date_passed"
        summary = (
            f"The renewal deal's next step ('{deal.next_step}') was due {day_label(deal.next_step_date)}."
        )
    elif age is not None and age >= cfg["stale_days"]:
        reason = "stale"
        summary = f"The renewal deal's next step ('{deal.next_step}') hasn't been updated in {age} days."
    else:
        return []
    label = f"Renewal deal next step: {deal.next_step or '(empty)'}" + (
        f" since {day_label(set_on)}" if set_on else ""
    )
    return [
        SignalHit(
            type="missing_next_step",
            polarity=Polarity.risk,
            value=age if age is not None else 0,
            summary=summary,
            facts={
                "reason": reason,
                "renewal_deal": deal.name,
                "next_step": deal.next_step,
                "next_step_set_on": set_on.isoformat() if set_on else None,
                "next_step_cleared": bool(not deal.next_step and deal.next_step_ever_set),
                "days_since_next_step_update": age,
            },
            evidence=(Evidence("field", f"{deal.id}:next_step", label, iso(deal.next_step_since)),),
        )
    ]
