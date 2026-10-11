"""Outcome tracker (Phase 6.1): what happened after an approved play was carried out.

For every change set with at least one line written to the tools, checkpoints at +7, +14 and +30
days compare the account *as of just before the play* with the account *as of the checkpoint* —
through the same as-of snapshots, signals and scoring as everything else (rule R3). Because the
checkpoint date is fixed, a late run measures exactly what an on-time run would have, and nothing
recorded after the checkpoint can leak in.

What it records: risk before → after, signals cleared and new, and the events a play hopes for —
the champion replied, the economic buyer engaged, a meeting was booked or held, the renewal got a
dated next step, usage moved. Checkpoints not yet due are stored as ``pending``.

What it is not: proof the play *caused* the change. Customers reply for their own reasons. Results
are labelled "measured after", and the plays table says how many sets each figure rests on.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from casefile.audit.log import append as audit
from casefile.cases.relationships import relationship_map
from casefile.db.models import (
    AccountCase,
    ChangeItem,
    ChangeSet,
    ExecutionAttempt,
    Org,
    PlayOutcome,
)
from casefile.scoring.points import score
from casefile.signals.base import run_signals
from casefile.timeline.snapshot import AccountHistory, Snapshot

CHECK_DAYS = (7, 14, 30)
MEANINGFUL = 10  # points: one signal's worth; smaller moves are "no change"
USAGE_DAYS = 14


@dataclass(frozen=True)
class Executed:
    change_set_id: uuid.UUID
    account_id: uuid.UUID
    case_kind: str
    playbook_id: str
    started_at: datetime  # first write attempt: "before" is the moment before this
    finished_at: datetime  # last successful write: checkpoints count from here


def executed_sets(session: Session, org: Org) -> list[Executed]:
    rows = session.execute(
        select(
            ChangeSet.id,
            AccountCase.account_id,
            AccountCase.kind,
            ChangeSet.playbook_id,
            func.min(ExecutionAttempt.started_at),
            func.max(ExecutionAttempt.finished_at).filter(ExecutionAttempt.status == "succeeded"),
        )
        .join(AccountCase, AccountCase.id == ChangeSet.case_id)
        .join(ChangeItem, ChangeItem.change_set_id == ChangeSet.id)
        .join(ExecutionAttempt, ExecutionAttempt.change_item_id == ChangeItem.id)
        .where(ChangeSet.org_id == org.id)
        .group_by(ChangeSet.id, AccountCase.account_id, AccountCase.kind, ChangeSet.playbook_id)
    ).all()
    return [
        Executed(cs, acc, kind.value, play, started, finished)
        for cs, acc, kind, play, started, finished in rows
        if started is not None and finished is not None
    ]


# ---------------------------------------------------------------- measuring


def _state(snap: Snapshot) -> dict[str, Any]:
    hits = run_signals(snap)
    s = score(snap, hits)
    return {
        "at": snap.as_of.isoformat(),
        "risk": s.risk,
        "opportunity": s.opportunity,
        "signals": sorted({h.type for h in hits}),
        "active_people": relationship_map(snap)["active_people"],
    }


def _avg_users(snap: Snapshot) -> float | None:
    lo = snap.today - timedelta(days=USAGE_DAYS)
    rows = [u.active_users for u in snap.usage if lo <= u.date < snap.today]
    return sum(rows) / len(rows) if rows else None


def _events(before: Snapshot, after: Snapshot) -> dict[str, Any]:
    t0, t1 = before.as_of, after.as_of
    champions = {c.email for c in after.contacts if c.role == "champion"}
    buyers = {c.email for c in after.contacts if c.role == "economic_buyer"}
    in_window = [a for a in after.activities if t0 < a.occurred_at <= t1]
    human_in = [
        a
        for a in in_window
        if a.type == "email"
        and a.direction == "inbound"
        and not a.extra.get("auto_reply")
        and not a.extra.get("bounce")
    ]
    held = [a for a in in_window if a.type == "meeting" and not a.cancelled]
    booked = [
        a for a in after.activities if a.type == "meeting" and not a.cancelled and t0 < a.known_at <= t1
    ]
    deal = after.renewal_deal()
    u0, u1 = _avg_users(before), _avg_users(after)
    return {
        "champion_replied": any(a.from_email in champions for a in human_in),
        "buyer_engaged": any(a.from_email in buyers for a in human_in)
        or any(buyers & set(a.participant_emails) for a in held),
        "meeting_booked": bool(booked),
        "meeting_held": bool(held),
        "next_step_set": bool(deal and deal.next_step and deal.next_step_since and deal.next_step_since > t0),
        "usage_change_pct": round((u1 - u0) / u0 * 100) if u0 and u1 is not None else None,
        "customer_emails": len(human_in),
    }


def classify(kind: str, before: dict[str, Any], after: dict[str, Any], events: dict[str, Any]) -> str:
    if kind == "expansion":
        # Growth plays aim at a conversation; the opportunity score barely moves in 30 days.
        return "improved" if events["meeting_booked"] or events["buyer_engaged"] else "no_change"
    delta = after["risk"] - before["risk"]
    if delta <= -MEANINGFUL:
        return "improved"
    if delta >= MEANINGFUL:
        return "worse"
    return "no_change"


def measure(history: AccountHistory, ex: Executed, at: datetime) -> dict[str, Any] | None:
    before = history.as_of(ex.started_at - timedelta(seconds=1))
    after = history.as_of(at)
    if before is None or after is None:
        return None
    b, a = _state(before), _state(after)
    ev = _events(before, after)
    return {
        "before": b,
        "after": {
            **a,
            "events": ev,
            "cleared": sorted(set(b["signals"]) - set(a["signals"])),
            "new": sorted(set(a["signals"]) - set(b["signals"])),
        },
        "result": classify(ex.case_kind, b, a, ev),
    }


# ---------------------------------------------------------------- the job


def track(session: Session, org: Org, *, now: datetime | None = None) -> dict[str, int]:
    """Make sure every executed set has its checkpoints; measure the ones that are due."""
    now = now or datetime.now(UTC)
    counts = {"sets": 0, "measured": 0, "pending": 0}
    for ex in executed_sets(session, org):
        counts["sets"] += 1
        for day in CHECK_DAYS:
            session.execute(
                insert(PlayOutcome)
                .values(org_id=org.id, change_set_id=ex.change_set_id, check_day=day, result="pending")
                .on_conflict_do_nothing(index_elements=["change_set_id", "check_day"])
            )
        due = [
            row
            for row in session.scalars(
                select(PlayOutcome).where(
                    PlayOutcome.change_set_id == ex.change_set_id, PlayOutcome.measured_at.is_(None)
                )
            )
            if ex.finished_at + timedelta(days=row.check_day) <= now
        ]
        unmeasured = session.scalar(
            select(func.count())
            .select_from(PlayOutcome)
            .where(PlayOutcome.change_set_id == ex.change_set_id, PlayOutcome.measured_at.is_(None))
        )
        counts["pending"] += (unmeasured or 0) - len(due)
        if not due:
            continue
        history = AccountHistory.load(session, ex.account_id)
        for row in due:
            m = measure(history, ex, ex.finished_at + timedelta(days=row.check_day))
            if m is None:
                continue
            row.signals_before, row.signals_after, row.result = m["before"], m["after"], m["result"]
            row.measured_at = now
            counts["measured"] += 1
            audit(
                session,
                org.id,
                "outcome_measured",
                entity_type="change_set",
                entity_id=str(ex.change_set_id),
                details={
                    "check_day": row.check_day,
                    "result": m["result"],
                    "risk_before": m["before"]["risk"],
                    "risk_after": m["after"]["risk"],
                },
            )
    return counts


# ---------------------------------------------------------------- reading


def account_outcomes(
    session: Session, account_id: uuid.UUID, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Every executed play on the account: stored checkpoints plus a live "so far" reading."""
    now = now or datetime.now(UTC)
    org_id = session.scalar(select(AccountCase.org_id).where(AccountCase.account_id == account_id).limit(1))
    if org_id is None:
        return []
    org = session.get(Org, org_id)
    assert org is not None
    mine = [ex for ex in executed_sets(session, org) if ex.account_id == account_id]
    if not mine:
        return []
    history = AccountHistory.load(session, account_id)
    out = []
    for ex in sorted(mine, key=lambda e: e.finished_at, reverse=True):
        rows = {
            r.check_day: r
            for r in session.scalars(select(PlayOutcome).where(PlayOutcome.change_set_id == ex.change_set_id))
        }
        checkpoints = []
        for day in CHECK_DAYS:
            row = rows.get(day)
            due = ex.finished_at + timedelta(days=day)
            measured = row is not None and row.measured_at is not None
            checkpoints.append(
                {
                    "day": day,
                    "due": due.isoformat(),
                    "result": row.result if measured and row else "pending",
                    "before": row.signals_before if measured and row else None,
                    "after": row.signals_after if measured and row else None,
                }
            )
        so_far = measure(history, ex, now) if now > ex.finished_at else None
        out.append(
            {
                "change_set_id": str(ex.change_set_id),
                "play": ex.playbook_id,
                "case_kind": ex.case_kind,
                "executed_at": ex.finished_at.isoformat(),
                "days_since": (now - ex.finished_at).days,
                "checkpoints": checkpoints,
                "so_far": so_far,
            }
        )
    return out


def plays_table(session: Session, org: Org) -> list[dict[str, Any]]:
    """Leaders' view: per play, how the executed sets turned out at each checkpoint."""
    executed = {ex.change_set_id: ex for ex in executed_sets(session, org)}
    rows = session.scalars(select(PlayOutcome).where(PlayOutcome.org_id == org.id)).all()
    by_play: dict[str, dict[str, Any]] = {}
    for ex in executed.values():
        by_play.setdefault(ex.playbook_id, {"play": ex.playbook_id, "sets": 0, "checkpoints": {}})
        by_play[ex.playbook_id]["sets"] += 1
    for r in rows:
        owner = executed.get(r.change_set_id)
        if owner is None:
            continue
        cp = by_play[owner.playbook_id]["checkpoints"].setdefault(
            r.check_day,
            {
                "day": r.check_day,
                "improved": 0,
                "no_change": 0,
                "worse": 0,
                "pending": 0,
                "_deltas": [],
                "_events": {"champion_replied": 0, "meeting_booked": 0},
            },
        )
        cp[r.result if r.result in cp else "pending"] += 1
        if r.measured_at is not None:
            cp["_deltas"].append(r.signals_after["risk"] - r.signals_before["risk"])
            for k in cp["_events"]:
                cp["_events"][k] += bool((r.signals_after.get("events") or {}).get(k))
    out = []
    for play in sorted(by_play.values(), key=lambda p: -p["sets"]):
        cps = []
        for day in CHECK_DAYS:
            cp = play["checkpoints"].get(day) or {
                "day": day,
                "improved": 0,
                "no_change": 0,
                "worse": 0,
                "pending": 0,
                "_deltas": [],
                "_events": {},
            }
            measured = len(cp["_deltas"])
            cps.append(
                {
                    "day": day,
                    "measured": measured,
                    "improved": cp["improved"],
                    "no_change": cp["no_change"],
                    "worse": cp["worse"],
                    "pending": cp["pending"],
                    "median_risk_change": statistics.median(cp["_deltas"]) if measured else None,
                    "champion_replied": cp["_events"].get("champion_replied", 0),
                    "meeting_booked": cp["_events"].get("meeting_booked", 0),
                }
            )
        out.append({"play": play["play"], "sets": play["sets"], "checkpoints": cps})
    return out
