"""Relationship signals from email and calendar: meetings, champion replies, departures, executives."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from casefile.db.models import Polarity
from casefile.signals.base import Evidence, SignalHit, day_label, iso, signal
from casefile.timeline.snapshot import ActivityView, ContactView, Snapshot

LEFT_COMPANY = re.compile(
    r"\b(ha(ve|s) left|no longer (with|work|at|employed)|left the company|moved on|last day at)\b", re.I
)


def _emails(snap: Snapshot) -> list[ActivityView]:
    return [a for a in snap.activities if a.type == "email" and a.occurred_at <= snap.as_of]


def _meeting_label(m: ActivityView, snap: Snapshot) -> str:
    return f"{m.subject or 'Meeting'} ({day_label(snap.local_date(m.occurred_at))})"


@signal(
    "meeting_gap", Polarity.risk, "calendar", "21+ days since the last customer meeting and nothing booked"
)
def meeting_gap(snap: Snapshot, cfg: dict[str, Any]) -> list[SignalHit]:
    meetings = [a for a in snap.activities if a.type == "meeting"]
    held = [m for m in meetings if not m.cancelled and m.occurred_at <= snap.as_of]
    booked = [m for m in meetings if not m.cancelled and m.occurred_at > snap.as_of]
    if not held or booked:
        return []  # never met (a blind spot, not this signal) or a meeting is already booked
    last = max(held, key=lambda m: m.occurred_at)
    last_day = snap.local_date(last.occurred_at)
    days = (snap.today - last_day).days
    if days < cfg["min_days"]:
        return []
    since = snap.as_of - timedelta(days=cfg["cancelled_lookback_days"])
    cancelled = [m for m in meetings if m.cancelled and m.occurred_at >= since]
    summary = f"The last customer meeting was {days} days ago ({day_label(last_day)}) and nothing is booked."
    if cancelled:
        summary += (
            f" {len(cancelled)} meeting(s) were cancelled in the last {cfg['cancelled_lookback_days']} days."
        )
    evidence = [
        Evidence(
            "activity", str(last.id), f"Last meeting: {_meeting_label(last, snap)}", iso(last.occurred_at)
        )
    ]
    evidence += [
        Evidence("activity", str(m.id), f"Cancelled: {_meeting_label(m, snap)}", iso(m.occurred_at))
        for m in cancelled
    ]
    return [
        SignalHit(
            type="meeting_gap",
            polarity=Polarity.risk,
            value=days,
            summary=summary,
            facts={
                "days_since_last_meeting": days,
                "last_meeting_date": last_day.isoformat(),
                "meetings_booked": 0,
                "meetings_cancelled_recently": len(cancelled),
                "cancelled_lookback_days": cfg["cancelled_lookback_days"],
            },
            evidence=tuple(evidence),
            window_start=last.occurred_at,
            window_end=snap.as_of,
        )
    ]


@signal("champion_silence", Polarity.risk, "email", "2+ emails to the champion over 10+ days with no reply")
def champion_silence(snap: Snapshot, cfg: dict[str, Any]) -> list[SignalHit]:
    emails = _emails(snap)
    known = {c.email for c in snap.contacts}
    since = snap.as_of - timedelta(days=cfg["lookback_days"])
    hits: list[SignalHit] = []
    skip_days = cfg.get("skip_if_departed_within_days")  # v4; absent in older rule versions
    for champ in snap.contacts_with_role("champion"):
        if skip_days and _departures(snap, champ, snap.as_of - timedelta(days=skip_days)):
            continue  # their silence is the departure, already scored once as champion_departure
        to_champ = [e for e in emails if e.direction == "outbound" and champ.email in e.participant_emails]
        threads = {e.thread_id for e in to_champ if e.thread_id}
        # A reply is a human inbound message from the champion — or from an address we don't know
        # inside a thread we started with the champion (their personal address). Auto-replies and
        # bounces are not replies.
        replies = [
            e
            for e in emails
            if e.direction == "inbound"
            and not e.extra.get("auto_reply")
            and not e.extra.get("bounce")
            and (e.from_email == champ.email or (e.thread_id in threads and e.from_email not in known))
        ]
        last_reply = max((r.occurred_at for r in replies), default=None)
        unanswered = sorted(
            (
                e
                for e in to_champ
                if e.occurred_at >= since and (last_reply is None or e.occurred_at > last_reply)
            ),
            key=lambda e: e.occurred_at,
        )
        if len(unanswered) < cfg["min_unanswered"]:
            continue
        first_day = snap.local_date(unanswered[0].occurred_at)
        span = (snap.today - first_day).days
        if span < cfg["min_span_days"]:
            continue
        name = champ.name or champ.email
        facts: dict[str, Any] = {
            "champion_name": name,
            "unanswered_emails": len(unanswered),
            "first_unanswered_date": first_day.isoformat(),
            "days_since_first_unanswered": span,
            "last_reply_date": snap.local_date(last_reply).isoformat() if last_reply else None,
            "days_since_last_reply": (snap.today - snap.local_date(last_reply)).days if last_reply else None,
        }
        summary = (
            f"{name} (champion) has not replied to {len(unanswered)} emails sent since "
            f"{day_label(first_day)} ({span} days)."
        )
        if last_reply:
            summary += f" Last reply: {day_label(snap.local_date(last_reply))}."
        evidence = [
            Evidence(
                "activity",
                str(e.id),
                f"Unanswered: {e.subject or 'email'} ({day_label(snap.local_date(e.occurred_at))})",
                iso(e.occurred_at),
            )
            for e in unanswered[-5:]
        ]
        hits.append(
            SignalHit(
                type="champion_silence",
                polarity=Polarity.risk,
                value=len(unanswered),
                summary=summary,
                facts=facts,
                evidence=tuple(evidence),
                window_start=unanswered[0].occurred_at,
                window_end=snap.as_of,
            )
        )
    return hits


def _departures(snap: Snapshot, champ: ContactView, since: datetime) -> list[tuple[str, Evidence, datetime]]:
    """Every sign since ``since`` that this champion has left: a bounce, an "I have left" auto-reply
    (or the AI reader's verified champion_left label), or the CRM marking them as left."""
    found: list[tuple[str, Evidence, datetime]] = []
    for e in _emails(snap):
        if e.occurred_at < since:
            continue
        if e.extra.get("bounce") and e.extra.get("failed_recipient") == champ.email:
            found.append(
                (
                    "email bounced",
                    Evidence(
                        "activity",
                        str(e.id),
                        f"Bounce: mail to {champ.email} not delivered",
                        iso(e.occurred_at),
                    ),
                    e.occurred_at,
                )
            )
        elif e.from_email == champ.email and (
            (e.extra.get("auto_reply") and LEFT_COMPANY.search(f"{e.subject or ''} {e.snippet or ''}"))
            or any(lab.label == "champion_left" and lab.status == "accepted" for lab in e.labels)
        ):
            found.append(
                (
                    "auto-reply says they left",
                    Evidence(
                        "activity",
                        str(e.id),
                        f"Auto-reply from {champ.email}: left the company",
                        iso(e.occurred_at),
                    ),
                    e.occurred_at,
                )
            )
    if champ.status == "left" and champ.status_since and champ.status_since >= since:
        found.append(
            (
                "CRM marked them as left",
                Evidence(
                    "contact",
                    f"{champ.id}:status",
                    f"CRM: {champ.name or champ.email} marked as left",
                    iso(champ.status_since),
                ),
                champ.status_since,
            )
        )
    return sorted(found, key=lambda f: f[2])


@signal(
    "champion_departure",
    Polarity.risk,
    "email+crm",
    "Champion bounce, 'I have left' auto-reply, or CRM status left",
)
def champion_departure(snap: Snapshot, cfg: dict[str, Any]) -> list[SignalHit]:
    since = snap.as_of - timedelta(days=cfg["lookback_days"])
    hits: list[SignalHit] = []
    for champ in snap.contacts_with_role("champion", active_only=False):
        found = _departures(snap, champ, since)
        if not found:
            continue
        first_day = snap.local_date(found[0][2])
        how = sorted({f[0] for f in found})
        name = champ.name or champ.email
        hits.append(
            SignalHit(
                type="champion_departure",
                polarity=Polarity.risk,
                value=len(found),
                summary=(
                    f"Champion {name} appears to have left ({'; '.join(how)}), "
                    f"first seen {day_label(first_day)}."
                ),
                facts={
                    "champion_name": name,
                    "detected_date": first_day.isoformat(),
                    "days_since_detected": (snap.today - first_day).days,
                    "detections": len(found),
                    "how": how,
                },
                evidence=tuple(f[1] for f in found[:5]),
                window_start=found[0][2],
                window_end=snap.as_of,
            )
        )
    return hits


@signal("executive_engagement", Polarity.positive, "calendar", "Economic buyer joined a meeting recently")
def executive_engagement(snap: Snapshot, cfg: dict[str, Any]) -> list[SignalHit]:
    since = snap.as_of - timedelta(days=cfg["lookback_days"])
    buyers = snap.contacts_with_role("economic_buyer")
    for m in sorted(
        (a for a in snap.activities if a.type == "meeting"), key=lambda a: a.occurred_at, reverse=True
    ):
        if m.cancelled or not (since <= m.occurred_at <= snap.as_of):
            continue
        buyer = next((b for b in buyers if b.email in m.participant_emails), None)
        if buyer is None:
            continue
        day = snap.local_date(m.occurred_at)
        days = (snap.today - day).days
        name = buyer.name or buyer.email
        return [
            SignalHit(
                type="executive_engagement",
                polarity=Polarity.positive,
                value=days,
                summary=f"Economic buyer {name} joined a meeting {days} days ago ({day_label(day)}).",
                facts={"buyer_name": name, "meeting_date": day.isoformat(), "days_since_meeting": days},
                evidence=(
                    Evidence(
                        "activity",
                        str(m.id),
                        f"Meeting with {name}: {_meeting_label(m, snap)}",
                        iso(m.occurred_at),
                    ),
                ),
                window_start=m.occurred_at,
                window_end=snap.as_of,
            )
        ]
    return []


@signal(
    "single_threaded",
    Polarity.risk,
    "email+calendar",
    "Only one person at the customer has emailed us or met us in 60 days",
)
def single_threaded(snap: Snapshot, cfg: dict[str, Any]) -> list[SignalHit]:
    since = snap.as_of - timedelta(days=cfg["lookback_days"])
    people = {c.email: c for c in snap.contacts if c.status == "active"}
    active: dict[str, list[ActivityView]] = {}
    for a in snap.activities:
        if not (since <= a.occurred_at <= snap.as_of):
            continue
        if a.type == "email":
            human = a.direction == "inbound" and not a.extra.get("auto_reply") and not a.extra.get("bounce")
            if human and a.from_email in people:
                active.setdefault(a.from_email, []).append(a)
        elif a.type == "meeting" and not a.cancelled:
            for e in a.participant_emails:
                if e in people:
                    active.setdefault(e, []).append(a)
    # Nobody at all is a different problem (meeting gap, champion silence); this is "one thread".
    if not 1 <= len(active) <= cfg["max_active_people"]:
        return []
    (email, acts), *_ = active.items()
    person = people[email]
    name = person.name or email
    others = len(people) - 1
    last = max(acts, key=lambda a: a.occurred_at)
    last_day = snap.local_date(last.occurred_at)
    summary = f"Only {name} has emailed or met us in the last {cfg['lookback_days']} days; " + (
        f"none of the other {others} known contacts has."
        if others
        else "no other contact at the customer is on record."
    )
    return [
        SignalHit(
            type="single_threaded",
            polarity=Polarity.risk,
            value=len(active),
            summary=summary,
            facts={
                "person_name": name,
                "person_role": person.role,
                "active_people": len(active),
                "other_known_contacts": others,
                "lookback_days": cfg["lookback_days"],
                "last_active_date": last_day.isoformat(),
            },
            evidence=tuple(
                Evidence(
                    "activity",
                    str(a.id),
                    f"{'Meeting' if a.type == 'meeting' else 'Email from'} {name} "
                    f"({day_label(snap.local_date(a.occurred_at))})",
                    iso(a.occurred_at),
                )
                for a in sorted(acts, key=lambda a: a.occurred_at, reverse=True)[:3]
            ),
            window_start=since,
            window_end=snap.as_of,
        )
    ]
