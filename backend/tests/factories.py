"""Hand-built snapshots for signal and scoring tests (no database)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from casefile.timeline.snapshot import (
    AccountView,
    ActivityView,
    ContactView,
    DealView,
    Snapshot,
    UsageDay,
)

TZ = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=TZ)  # local noon
TODAY = NOW.date()
OWNER = "owner@vendor.test"


def at(days_ago: float, hour: int = 11, minute: int = 0) -> datetime:
    d = TODAY - timedelta(days=int(days_ago))
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=TZ)


def contact(
    email: str,
    role: str = "champion",
    *,
    status: str = "active",
    name: str | None = None,
    status_since: datetime | None = None,
    origin: str = "crm",
) -> ContactView:
    return ContactView(
        uuid.uuid4(), email, name or email.split("@")[0].title(), None, role, status, origin, status_since
    )


def deal(
    *,
    close_in_days: int | None = 43,
    next_step: str = "",
    since_days_ago: int | None = 20,
    ever_set: bool = True,
    stage: str = "qualifiedtobuy",
    amount: str = "1000000",
    deal_type: str = "renewal",
    next_step_date: date | None = None,
) -> DealView:
    closed = stage in {"closedwon", "closedlost"}
    return DealView(
        id=uuid.uuid4(),
        name="Renewal",
        deal_type=deal_type,
        stage=stage,
        is_closed=closed,
        is_won=stage == "closedwon",
        amount=Decimal(amount),
        close_date=TODAY + timedelta(days=close_in_days) if close_in_days is not None else None,
        next_step=next_step,
        next_step_since=at(since_days_ago) if since_days_ago is not None else None,
        next_step_ever_set=ever_set,
        next_step_date=next_step_date,
    )


def _activity(kind: str, when: datetime, **kw: Any) -> ActivityView:
    return ActivityView(
        id=kw.get("id", uuid.uuid4()),
        type=kind,
        direction=kw.get("direction", "outbound"),
        source=kw.get("source", "gmail" if kind == "email" else "gcal"),
        occurred_at=when,
        known_at=kw.get("known_at", when),
        cancelled=kw.get("cancelled", False),
        subject=kw.get("subject", kind),
        snippet=kw.get("snippet", ""),
        from_email=kw.get("from_email"),
        participant_emails=tuple(kw.get("participants", ())),
        contact_ids=tuple(kw.get("contact_ids", ())),
        thread_id=kw.get("thread_id"),
        extra=kw.get("extra", {}),
        injection_flag=False,
        match_method="contact_email",
        match_confidence=1.0,
    )


def email_out(when: datetime, to: str, *, thread: str = "t1", subject: str = "Hello") -> ActivityView:
    return _activity(
        "email",
        when,
        direction="outbound",
        from_email=OWNER,
        participants=[OWNER, to],
        thread_id=thread,
        subject=subject,
    )


def email_in(
    when: datetime,
    frm: str,
    *,
    thread: str = "t1",
    auto_reply: bool = False,
    bounce_for: str | None = None,
    snippet: str = "",
    subject: str = "Re: Hello",
) -> ActivityView:
    extra: dict[str, Any] = {"auto_reply": auto_reply, "bounce": bounce_for is not None}
    if bounce_for:
        extra["failed_recipient"] = bounce_for
    return _activity(
        "email",
        when,
        direction="inbound",
        from_email=frm,
        participants=[frm, OWNER],
        thread_id=thread,
        extra=extra,
        snippet=snippet,
        subject=subject,
    )


def meeting(
    when: datetime, attendees: list[str], *, cancelled: bool = False, booked_days_before: int = 10
) -> ActivityView:
    return _activity(
        "meeting",
        when,
        participants=[OWNER, *attendees],
        cancelled=cancelled,
        known_at=when - timedelta(days=booked_days_before),
        subject="Check-in",
    )


def usage_days(
    spec: dict[tuple[int, int], tuple[int, int]],
    *,
    seats_used: int | None = None,
    departments: dict[tuple[int, int], dict[str, int]] | None = None,
) -> list[UsageDay]:
    """``{(from_days_ago, to_days_ago_exclusive): (active_users, key_actions)}`` filled day by day."""
    rows: dict[date, UsageDay] = {}
    for (start, end), (users, actions) in spec.items():
        for n in range(start, end, -1):
            d = TODAY - timedelta(days=n)
            depts = next((v for (s, e), v in (departments or {}).items() if s >= n > e), {})
            rows[d] = UsageDay(d, users, actions, seats_used, depts)
    return [rows[d] for d in sorted(rows)]


def snapshot(
    *,
    activities: list[ActivityView] | None = None,
    contacts: list[ContactView] | None = None,
    deals: list[DealView] | None = None,
    usage: list[UsageDay] | None = None,
    renewal_in_days: int | None = 43,
    seats: int | None = 100,
    arr: str | None = "1000000",
    sources: set[str] | None = None,
    as_of: datetime = NOW,
) -> Snapshot:
    deals = deals if deals is not None else []
    open_renewal = next((d for d in deals if d.deal_type == "renewal" and not d.is_closed), None)
    acts = sorted(activities or [], key=lambda a: a.occurred_at)
    srcs = (
        sources
        if sources is not None
        else {"crm"} | {a.source for a in acts} | ({"usage"} if usage else set())
    )
    return Snapshot(
        as_of=as_of,
        tz=TZ,
        account=AccountView(
            id=uuid.uuid4(),
            name="Acme",
            domains=("acme.test",),
            owner_user_id=None,
            seats_purchased=seats,
            renewal_date=TODAY + timedelta(days=renewal_in_days) if renewal_in_days is not None else None,
            arr=Decimal(arr) if arr is not None else None,
            renewal_deal_id=open_renewal.id if open_renewal else None,
        ),
        contacts=tuple(contacts or []),
        deals=tuple(deals),
        activities=tuple(acts),
        usage=tuple(usage or []),
        sources=frozenset(srcs),
    )
