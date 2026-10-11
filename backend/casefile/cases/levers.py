"""What would lower this risk? (Phase 6.2)

Pure code, no AI. A *lever* is a change in the world — "a meeting is booked", "the champion
replies". We apply it to a copy of the account's snapshot and re-run the live signals and scoring on
that copy, so the answer comes from the same rules that produced the score (rule R3). That also
catches interactions a points table would miss: a meeting with the economic buyer both ends a meeting
gap and earns the executive-engagement credit.

What this is not: a forecast. It says what the current rules would do *if* the change happened, not
that it will happen or that the customer will renew. Some risk signals cannot be cleared by any action
under the current rules (a departed champion, negative emails); for those we report the date the rule
stops counting them, and say plainly that the drop on that date is the rule expiring, not recovery.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from itertools import combinations
from typing import Any

from casefile.actions.playbooks import Play, load_playbooks
from casefile.db.models import Polarity
from casefile.scoring.points import default_scoring, score
from casefile.signals.base import (
    REGISTRY,
    SignalHit,
    apply_role_fallbacks,
    day_label,
    default_thresholds,
    run_signals,
)
from casefile.timeline.snapshot import ActivityView, ContactView, DealView, Snapshot, UsageDay

LEVERS_VERSION = "levers-2"
_NS = uuid.UUID("6a0f3c1e-2b7d-4c55-9a8e-0c2f6d1b7e42")  # stable ids for the hypothetical records
MAX_EXPIRY_DAYS = 400
MAX_USAGE_STEPS = 60

Transform = Callable[[Snapshot], Snapshot]


@dataclass(frozen=True)
class Lever:
    key: str
    text: str  # what has to happen, in plain words
    detail: str  # how the rules see it
    transform: Transform = field(repr=False, compare=False)
    plays: tuple[str, ...] = ()
    direct_action: str | None = None  # an action type that makes this change itself once executed


def _hypothetical_id(snap: Snapshot, key: str) -> uuid.UUID:
    return uuid.uuid5(_NS, f"{snap.account.id}:{snap.as_of.isoformat()}:{key}")


def _activity(snap: Snapshot, key: str, kind: str, **kw: Any) -> ActivityView:
    when = kw["occurred_at"]
    return ActivityView(
        id=_hypothetical_id(snap, key),
        type=kind,
        direction=kw.get("direction", "outbound"),
        source="gmail" if kind == "email" else "gcal",
        occurred_at=when,
        known_at=when,
        cancelled=False,
        subject=kw.get("subject"),
        snippet=None,
        from_email=kw.get("from_email"),
        participant_emails=tuple(kw.get("participants", ())),
        contact_ids=tuple(kw.get("contact_ids", ())),
        thread_id=None,
        extra={},
        injection_flag=False,
        match_method="contact_email",
        match_confidence=1.0,
    )


def _add(snap: Snapshot, *acts: ActivityView) -> Snapshot:
    return replace(snap, activities=tuple(sorted((*snap.activities, *acts), key=lambda a: a.occurred_at)))


def _risk_types(hits: list[SignalHit]) -> set[str]:
    return {h.type for h in hits if h.polarity == Polarity.risk}


def _positive_types(hits: list[SignalHit]) -> set[str]:
    return {h.type for h in hits if h.polarity == Polarity.positive}


def _plays_for(plays: list[Play], signals: set[str]) -> tuple[str, ...]:
    return tuple(p.id for p in plays if (set(p.all_of) | set(p.any_of)) & signals)


# ---------------------------------------------------------------- lever builders (one per kind of change)


def _meeting_booked(snap: Snapshot, hits: list[SignalHit], plays: list[Play]) -> list[Lever]:
    if "meeting_gap" not in _risk_types(hits):
        return []
    who = next(iter(snap.contacts_with_role("champion")), None) or next(iter(snap.contacts), None)

    def apply(s: Snapshot) -> Snapshot:
        return _add(
            s,
            _activity(
                s,
                "meeting_booked",
                "meeting",
                occurred_at=s.as_of + timedelta(days=2),
                subject="Check-in",
                participants=[who.email] if who else [],
            ),
        )

    return [
        Lever(
            "meeting_booked",
            "A customer meeting is booked",
            "Any meeting on the calendar ends the meeting gap. It comes back 21 days after that meeting "
            "if nothing else is booked.",
            apply,
            _plays_for(plays, {"meeting_gap"}),
        )
    ]


def _buyer_meeting(snap: Snapshot, hits: list[SignalHit], plays: list[Play]) -> list[Lever]:
    buyer = next(iter(snap.contacts_with_role("economic_buyer")), None)
    if buyer is None or "executive_engagement" in _positive_types(hits):
        return []
    name = buyer.name or buyer.email

    def apply(s: Snapshot) -> Snapshot:
        return _add(
            s,
            _activity(
                s,
                "buyer_meeting",
                "meeting",
                occurred_at=s.as_of - timedelta(minutes=1),
                subject="Meeting",
                participants=[buyer.email],
                contact_ids=[buyer.id],
            ),
        )

    to_buyer = tuple(p.id for p in plays if p.recipient == "economic_buyer")
    return [
        Lever(
            "buyer_meeting",
            f"{name} (economic buyer) joins a meeting",
            "A meeting the economic buyer attends counts in our favour for 60 days, and any held meeting "
            "ends a meeting gap.",
            apply,
            tuple(dict.fromkeys(to_buyer + _plays_for(plays, {"meeting_gap"}))),
        )
    ]


def _champion_replies(snap: Snapshot, hits: list[SignalHit], plays: list[Play]) -> list[Lever]:
    silent = {h.facts.get("champion_name") for h in hits if h.type == "champion_silence"}
    # Never suggest waiting for a reply from someone we believe has left (bounce, "I have left"
    # auto-reply) — the CRM can still list them as active for weeks.
    gone = {h.facts.get("champion_name") for h in hits if h.type == "champion_departure"}
    champs = [c for c in snap.contacts_with_role("champion") if (c.name or c.email) in silent - gone]
    if not champs:
        return []

    def apply(s: Snapshot) -> Snapshot:
        return _add(
            s,
            *(
                _activity(
                    s,
                    f"reply:{c.id}",
                    "email",
                    direction="inbound",
                    occurred_at=s.as_of - timedelta(minutes=1),
                    from_email=c.email,
                    participants=[c.email],
                    contact_ids=[c.id],
                    subject="Re:",
                )
                for c in champs
            ),
        )

    names = " and ".join(c.name or c.email for c in champs)
    return [
        Lever(
            "champion_replies",
            f"{names} {'replies' if len(champs) == 1 else 'reply'}",
            "A reply from the champion (not an auto-reply) ends the silence.",
            apply,
            _plays_for(plays, {"champion_silence"}),
        )
    ]


def _second_person(snap: Snapshot, hits: list[SignalHit], plays: list[Play]) -> list[Lever]:
    hit = next((h for h in hits if h.type == "single_threaded"), None)
    if hit is None:
        return []
    only = hit.facts.get("person_name")
    others = [c for c in snap.contacts if c.status == "active" and (c.name or c.email) != only]
    # Prefer the economic buyer, then anyone else we know; if nobody else is on record, the change
    # is finding a new contact.
    others.sort(key=lambda c: {"economic_buyer": 0, "champion": 1}.get(c.role, 2))
    who = others[0] if others else None
    domain = snap.account.domains[0] if snap.account.domains else "customer.test"
    email = who.email if who else f"new.contact@{domain}"

    def apply(s: Snapshot) -> Snapshot:
        added = s
        if who is None:
            new = ContactView(
                _hypothetical_id(s, "second_person"), email, "", None, "unknown", "active", "inferred"
            )
            added = replace(s, contacts=(*s.contacts, new))
        return _add(
            added,
            _activity(
                added,
                "second_person",
                "email",
                direction="inbound",
                occurred_at=s.as_of - timedelta(minutes=1),
                from_email=email,
                participants=[email],
                subject="Re:",
            ),
        )

    text = (
        f"A second person engages — for example {who.name or who.email} replies or joins a meeting"
        if who
        else "A second contact at the customer is found and engages"
    )
    return [
        Lever(
            "second_person",
            text,
            f"Only {only} has emailed or met us recently. Anyone else at the customer writing to us or "
            "joining a meeting ends the single thread.",
            apply,
            tuple(
                dict.fromkeys(
                    _plays_for(plays, {"single_threaded"})
                    + tuple(p.id for p in plays if p.recipient == "economic_buyer")
                )
            ),
        )
    ]


def _next_step_set(snap: Snapshot, hits: list[SignalHit], plays: list[Play]) -> list[Lever]:
    hit = next((h for h in hits if h.type == "missing_next_step"), None)
    if hit is None:
        return []
    no_deal = hit.facts.get("reason") == "no_renewal_deal"

    def apply(s: Snapshot) -> Snapshot:
        fresh: dict[str, Any] = {
            "next_step": "Next step agreed",
            "next_step_since": s.as_of,
            "next_step_ever_set": True,
            "next_step_date": s.today + timedelta(days=14),
        }
        deal = s.renewal_deal()
        if deal is not None:
            return replace(s, deals=tuple(replace(d, **fresh) if d.id == deal.id else d for d in s.deals))
        new = DealView(
            id=_hypothetical_id(s, "renewal_deal"),
            name="Renewal",
            deal_type="renewal",
            stage="qualifiedtobuy",
            is_closed=False,
            is_won=False,
            amount=s.account.arr,
            close_date=s.account.renewal_date,
            **fresh,
        )
        return replace(s, deals=(*s.deals, new), account=replace(s.account, renewal_deal_id=new.id))

    with_step = tuple(p.id for p in plays if any(a.get("type") == "crm_next_step" for a in p.actions))
    return [
        Lever(
            "next_step_set",
            "A renewal deal is opened in the CRM with a next step"
            if no_deal
            else "A dated next step is recorded on the renewal deal",
            "The rule wants an open renewal deal whose next step is set, not overdue and updated in the "
            "last 30 days.",
            apply,
            tuple(dict.fromkeys(with_step + _plays_for(plays, {"missing_next_step"}))),
            direct_action=None if no_deal else "crm_next_step",
        )
    ]


def _usage_window(snap: Snapshot, start_days_ago: int, end_days_ago: int) -> list[UsageDay]:
    lo, hi = snap.today - timedelta(days=start_days_ago), snap.today - timedelta(days=end_days_ago)
    return [u for u in snap.usage if lo <= u.date < hi]


def _usage_recovers(
    snap: Snapshot, hits: list[SignalHit], plays: list[Play], thresholds: dict[str, Any]
) -> list[Lever]:
    if "usage_decline" not in _risk_types(hits):
        return []
    cfg = thresholds["usage_decline"]
    w, b = cfg["window_days"], cfg["baseline_days"]
    recent, earlier = _usage_window(snap, w, 0), _usage_window(snap, 2 * w, w)
    baseline = _usage_window(snap, w + b, w)
    if not recent or not earlier:
        return []

    def avg(rows: list[UsageDay], attr: str) -> float:
        return sum(getattr(u, attr) for u in rows) / len(rows)

    users_now, actions_now = avg(recent, "active_users"), avg(recent, "key_actions")
    # The rule compares rounded percentages; half a point of margin keeps us on the safe side.
    keep = 1 - (cfg["min_drop_pct"] - 0.5) / 100
    users_need = avg(earlier, "active_users") * keep
    if len(baseline) >= b // 2:
        slow_keep = 1 - (cfg["min_drop_pct_vs_baseline"] - 0.5) / 100
        users_need = max(users_need, avg(baseline, "active_users") * slow_keep)
    actions_need = avg(earlier, "key_actions") * keep
    users_t = max(math.ceil(users_need), math.ceil(users_now))
    actions_t = max(math.ceil(actions_need), math.ceil(actions_now))
    fix_users, fix_actions = users_now < users_need, actions_now < actions_need
    if not (fix_users or fix_actions):
        fix_users = True  # fired on a rounding edge: lift users until the rule agrees

    def recovered(users: int, actions: int) -> Transform:
        def apply(s: Snapshot) -> Snapshot:
            lo, hi = s.today - timedelta(days=w), s.today
            rows = tuple(
                replace(
                    u,
                    active_users=users if fix_users else u.active_users,
                    key_actions=actions if fix_actions else u.key_actions,
                )
                if lo <= u.date < hi
                else u
                for u in s.usage
            )
            return replace(s, usage=rows)

        return apply

    # Start at the arithmetic target and step up until the real signal code agrees usage has
    # recovered. The verified numbers are what we show.
    fn = REGISTRY["usage_decline"].fn
    for _ in range(MAX_USAGE_STEPS):
        if not fn(recovered(users_t, actions_t)(snap), cfg):
            break
        users_t += 1 if fix_users else 0
        actions_t += max(1, actions_t // 50) if fix_actions else 0
    else:
        return []
    parts = []
    if fix_users:
        parts.append(f"{users_t} active users a day (now {users_now:.0f})")
    if fix_actions:
        parts.append(f"{actions_t} key actions a day (now {actions_now:.0f})")
    if not parts:
        return []
    return [
        Lever(
            "usage_recovers",
            f"Usage recovers to at least {' and '.join(parts)}, averaged over {w} days",
            f"Measured against today's comparison windows ({w} days before, and {b} days before that); "
            "the windows move every day.",
            recovered(users_t, actions_t),
            _plays_for(plays, {"usage_decline"}),
        )
    ]


# ---------------------------------------------------------------- the analysis


def _risk(snap: Snapshot, hits: list[SignalHit], scoring: dict[str, Any]) -> int:
    return score(snap, hits, scoring).risk


def _expiry(snap: Snapshot, signal_type: str, thresholds: dict[str, Any]) -> str | None:
    """First day the signal stops firing if nothing new happens (stepping the clock forward)."""
    spec = REGISTRY.get(signal_type)
    if spec is None:
        return None
    for days in range(1, MAX_EXPIRY_DAYS + 1):
        later = replace(snap, as_of=snap.as_of + timedelta(days=days))
        if not spec.fn(later, thresholds.get(signal_type, {})):
            return later.today.isoformat()
    return None


def what_would_lower_risk(
    snap: Snapshot,
    hits: list[SignalHit],
    *,
    thresholds: dict[str, Any] | None = None,
    scoring: dict[str, Any] | None = None,
    plan_play: str | None = None,
    plan_done: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """``plan_play`` is the play of the case's current plan; ``plan_done`` the action types it has
    already written to the tools. A lever its plan has done but the score still shows was written but
    not yet read back (in the sandbox, CRM reads come from synthetic data, so it never is)."""
    thresholds = thresholds or default_thresholds()
    scoring = scoring or default_scoring()
    snap = apply_role_fallbacks(snap, thresholds)  # same likely-champion inference as the signals
    plays, _ = load_playbooks()
    risk_now = _risk(snap, hits, scoring)
    close_below = scoring["cases"]["close_risk_below"]
    before_risk, before_pos = _risk_types(hits), _positive_types(hits)

    candidates = [
        *_champion_replies(snap, hits, plays),
        *_meeting_booked(snap, hits, plays),
        *_buyer_meeting(snap, hits, plays),
        *_second_person(snap, hits, plays),
        *_next_step_set(snap, hits, plays),
        *_usage_recovers(snap, hits, plays, thresholds),
    ]
    levers: list[tuple[Lever, dict[str, Any]]] = []
    for lever in candidates:
        after = lever.transform(snap)
        after_hits = run_signals(after, thresholds)
        clears = sorted(before_risk - _risk_types(after_hits))
        adds = sorted(_positive_types(after_hits) - before_pos)
        new_risk = _risk(after, after_hits, scoring)
        if new_risk >= risk_now and not clears:
            continue  # the rules would not move; don't suggest it
        levers.append(
            (
                lever,
                {
                    "key": lever.key,
                    "text": lever.text,
                    "detail": lever.detail,
                    "clears": clears,
                    "adds": adds,
                    "risk_after": new_risk,
                    "change": new_risk - risk_now,
                    "plays": list(lever.plays),
                    "in_plan": plan_play in lever.plays if plan_play else False,
                    "direct_action": lever.direct_action,
                    "done_by_plan": lever.direct_action in plan_done if lever.direct_action else False,
                },
            )
        )
    levers.sort(key=lambda lv: (lv[1]["change"], lv[0].key))
    cleared_by_action = {s for _, view in levers for s in view["clears"]}

    # Signals no action clears: report when the rule stops counting them.
    expiries = []
    for t in sorted(before_risk - cleared_by_action):
        without = [h for h in hits if h.type != t]
        on = _expiry(snap, t, thresholds)
        expiries.append(
            {
                "signal": t,
                "points": scoring["risk_points"].get(t, 0),
                "stops_counting_on": on,
                "risk_after": _risk(snap, without, scoring),
                "text": (
                    f"No action clears this under the current rules. It stops counting on "
                    f"{day_label(date.fromisoformat(on))} if nothing new happens; that drop is the rule "
                    "expiring, not a sign of recovery."
                    if on
                    else "No action clears this under the current rules."
                ),
            }
        )

    return {
        "version": LEVERS_VERSION,
        "rules": {
            "scoring": scoring["version"],
            "thresholds": thresholds["version"],
            "playbooks": plays[0].version if plays else None,
        },
        "risk_now": risk_now,
        "close_below": close_below,
        "open_at": scoring["cases"]["open_risk_at"],
        "levers": [view for _, view in levers],
        "expiries": expiries,
        "to_close": _to_close(snap, levers, thresholds, scoring, risk_now, close_below),
        "plan_play": plan_play,
    }


def _to_close(
    snap: Snapshot,
    levers: list[tuple[Lever, dict[str, Any]]],
    thresholds: dict[str, Any],
    scoring: dict[str, Any],
    risk_now: int,
    close_below: int,
) -> dict[str, Any]:
    """The fewest changes that, together, take risk below the close threshold."""
    if risk_now < close_below:
        return {"needed": False, "possible": True, "levers": [], "risk_after": risk_now}
    best: tuple[int, list[str]] = (risk_now, [])
    for size in range(1, len(levers) + 1):
        found: tuple[int, list[str]] | None = None
        for combo in combinations(levers, size):
            s = snap
            for lever, _ in combo:
                s = lever.transform(s)
            r = _risk(s, run_signals(s, thresholds), scoring)
            keys = [view["key"] for _, view in combo]
            if r < best[0]:
                best = (r, keys)
            if r < close_below and (found is None or r < found[0]):
                found = (r, keys)
        if found is not None:
            return {"needed": True, "possible": True, "levers": found[1], "risk_after": found[0]}
    return {"needed": True, "possible": False, "levers": best[1], "risk_after": best[0]}
