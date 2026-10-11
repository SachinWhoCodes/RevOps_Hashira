"""Relationship map (Phase 6.3): who at the customer we talk to, how often, and when we last heard
from each of them. A pure function of an as-of snapshot, so the time machine shows the map as it was.

"Heard from" means the person did something: wrote to us (not an auto-reply or bounce) or was in a
meeting that took place. Emails we sent count as contact, not as hearing from them. Meeting invites
list who was invited, not who turned up — an accepted approximation, as in ``single_threaded``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from casefile.timeline.snapshot import Snapshot

ROLE_ORDER = {"champion": 0, "economic_buyer": 1, "user": 2, "unknown": 3}


def relationship_map(snap: Snapshot, *, window_days: int = 90, active_days: int = 60) -> dict[str, Any]:
    since = snap.as_of - timedelta(days=window_days)
    active_since = snap.as_of - timedelta(days=active_days)
    people: dict[str, dict[str, Any]] = {
        c.email: {
            "id": str(c.id),
            "name": c.name,
            "email": c.email,
            "title": c.title,
            "role": c.role,
            "role_inferred": c.role_inferred,
            "status": c.status,
            "origin": c.origin,
            "emails_from": 0,
            "emails_to": 0,
            "meetings": 0,
            "_heard": None,
            "_contacted": None,
            "_next_meeting": None,
        }
        for c in snap.contacts
    }

    def later(a: datetime | None, b: datetime) -> datetime:
        return b if a is None or b > a else a

    for act in snap.activities:
        recent = act.occurred_at >= since
        if act.type == "email" and act.occurred_at <= snap.as_of:
            human_reply = (
                act.direction == "inbound" and not act.extra.get("auto_reply") and not act.extra.get("bounce")
            )
            if human_reply and act.from_email in people:
                p = people[act.from_email]
                p["_heard"] = later(p["_heard"], act.occurred_at)
                p["emails_from"] += recent
            if act.direction == "outbound":
                for e in act.participant_emails:
                    if e in people:
                        p = people[e]
                        p["_contacted"] = later(p["_contacted"], act.occurred_at)
                        p["emails_to"] += recent
        elif act.type == "meeting" and not act.cancelled:
            for e in act.participant_emails:
                if e not in people:
                    continue
                p = people[e]
                if act.occurred_at <= snap.as_of:
                    p["_heard"] = later(p["_heard"], act.occurred_at)
                    p["_contacted"] = later(p["_contacted"], act.occurred_at)
                    p["meetings"] += recent
                elif p["_next_meeting"] is None or act.occurred_at < p["_next_meeting"]:
                    p["_next_meeting"] = act.occurred_at

    rows = []
    for p in people.values():
        heard, contacted, upcoming = p.pop("_heard"), p.pop("_contacted"), p.pop("_next_meeting")
        rows.append(
            {
                **p,
                "last_heard_from": snap.local_date(heard).isoformat() if heard else None,
                "days_since_heard": (snap.today - snap.local_date(heard)).days if heard else None,
                "last_contacted": snap.local_date(contacted).isoformat() if contacted else None,
                "next_meeting": snap.local_date(upcoming).isoformat() if upcoming else None,
                "active": bool(heard and heard >= active_since and p["status"] == "active"),
            }
        )
    rows.sort(
        key=lambda r: (
            not r["active"],
            r["status"] != "active",
            ROLE_ORDER.get(r["role"], 9),
            r["days_since_heard"] if r["days_since_heard"] is not None else 10**6,
        )
    )
    return {
        "window_days": window_days,
        "active_days": active_days,
        "active_people": sum(r["active"] for r in rows),
        "known_people": sum(r["status"] == "active" for r in rows),
        "people": rows,
    }
