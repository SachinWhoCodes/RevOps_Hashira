"""Google Calendar events → meeting activities.

``known_at`` is the event's creation time: a meeting booked in March for May counts as "booked"
in an as-of-April snapshot. Cancellations keep the row and set ``cancelled_at``. Events with no
external attendee are internal and skipped.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from casefile.connectors.base import RawItem
from casefile.db.models import Activity, ActivityType, Direction, MatchMethod
from casefile.ingest.context import NormalizeContext
from casefile.ingest.text import snippet
from casefile.ingest.util import email_domain, is_system_address, parse_date, parse_ts

SOURCE = "gcal"


def _start(event: dict[str, Any], tz: str) -> datetime | None:
    start = event.get("start") or {}
    if start.get("dateTime"):
        return parse_ts(start["dateTime"])
    day = parse_date(start.get("date"))
    return datetime.combine(day, time(9, 0), tzinfo=ZoneInfo(tz)) if day else None


def parse_event(event: dict[str, Any], internal_domains: set[str], tz: str) -> dict[str, Any] | None:
    start = _start(event, tz)
    created = parse_ts(event.get("created"))
    if start is None or created is None:
        return None
    attendees = [a for a in event.get("attendees") or [] if a.get("email")]
    organizer = ((event.get("organizer") or {}).get("email") or "").lower()
    emails = list(dict.fromkeys([organizer, *[a["email"].lower() for a in attendees]]))
    emails = [e for e in emails if e and not is_system_address(e) and not e.endswith("calendar.google.com")]
    external = [e for e in emails if email_domain(e) not in internal_domains]
    if not external:
        return None
    cancelled = event.get("status") == "cancelled"
    responses = {a["email"].lower(): a.get("responseStatus") for a in attendees}
    end = parse_ts((event.get("end") or {}).get("dateTime"))
    return {
        "type": ActivityType.meeting,
        "direction": Direction.outbound if email_domain(organizer) in internal_domains else Direction.inbound,
        "occurred_at": start,
        "known_at": created,
        "cancelled_at": parse_ts(event.get("updated")) if cancelled else None,
        "source": SOURCE,
        # The event id is stable across reschedules and shared by attendees' copies of the event.
        "source_id": str(event.get("id") or event.get("iCalUID")),
        "dedupe_key": event.get("iCalUID"),
        "subject": event.get("summary"),
        "snippet": snippet(event.get("description") or ""),
        "from_email": organizer or None,
        "participant_emails": emails,
        "extra": {
            "calendar": (event.get("_casefile") or {}).get("calendar"),
            "event_id": event.get("id"),
            "status": event.get("status"),
            "end": end.isoformat() if end else None,
            "responses": responses,
        },
        "match_method": MatchMethod.unmatched,
        "match_confidence": 0.0,
    }


def events(ctx: NormalizeContext, items: list[RawItem]) -> None:
    by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        parsed = parse_event(item.payload, ctx.internal_domains, ctx.org.timezone)
        if parsed:
            parsed["org_id"] = ctx.org_id
            by_id[parsed["source_id"]] = parsed  # attendees' copies of one event collapse to one row
        elif item.payload.get("status") == "cancelled" and item.payload.get("id"):
            # Incremental sync sends cancellations as bare {id, status}: mark the meeting we have.
            ctx.session.execute(
                update(Activity)
                .where(
                    Activity.org_id == ctx.org_id,
                    Activity.source == SOURCE,
                    Activity.source_id == str(item.payload["id"]),
                    Activity.cancelled_at.is_(None),
                )
                .values(cancelled_at=parse_ts(item.payload.get("updated")) or ctx.now())
            )
    if not by_id:
        return
    stmt = insert(Activity).values(list(by_id.values()))
    # Events change (rescheduled, cancelled, renamed): update those fields, keep the identity match.
    # Known limitation: a reschedule overwrites the old time, so history of reschedules is not kept.
    mutable = ["occurred_at", "cancelled_at", "subject", "snippet", "participant_emails", "extra"]
    ctx.session.execute(
        stmt.on_conflict_do_update(
            index_elements=["org_id", "source", "source_id"], set_={k: stmt.excluded[k] for k in mutable}
        )
    )
