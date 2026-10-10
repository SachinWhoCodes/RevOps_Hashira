"""Property test for rule R3: an as-of snapshot never contains anything knowable only later.

Histories are generated at random (no database), then sliced at random times.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

from casefile.db.models import (
    Account,
    Activity,
    ActivityType,
    Deal,
    DealType,
    Direction,
    MatchMethod,
    UsageDaily,
)
from casefile.timeline.snapshot import AccountHistory

T0 = datetime(2025, 1, 1, tzinfo=UTC)
offsets = st.integers(min_value=0, max_value=600 * 24)  # hours after T0


def _at(hours: int) -> datetime:
    return T0 + timedelta(hours=hours)


@st.composite
def histories(draw: st.DrawFn) -> tuple[AccountHistory, list[tuple[datetime, str]]]:
    h = AccountHistory()
    acc_id = uuid.uuid4()
    h.account = Account(id=acc_id, name="Acme", domains=["acme.test"], created_in_source_at=_at(0))
    h.tz = ZoneInfo("Asia/Kolkata")
    h.won, h.lost = {"closedwon"}, {"closedlost"}
    h.fields.add(acc_id, "exists", _at(0), True)

    deal_id = uuid.uuid4()
    h.deals = [Deal(id=deal_id, name="Renewal", deal_type=DealType.renewal, created_in_source_at=_at(0))]
    h.fields.add(deal_id, "exists", _at(0), True)
    h.fields.add(deal_id, "deal_type", _at(0), "renewal")
    steps = sorted(set(draw(st.lists(offsets, min_size=1, max_size=8))))
    next_steps = []
    for i, off in enumerate(steps):
        value = f"step {i}"
        h.fields.add(deal_id, "next_step", _at(off), value)
        next_steps.append((_at(off), value))

    for _ in range(draw(st.integers(0, 25))):
        occurred = _at(draw(offsets))
        known = occurred - timedelta(hours=draw(st.integers(0, 24 * 30)))  # booked ahead of time
        cancelled = _at(draw(offsets)) if draw(st.booleans()) else None
        h.activities.append(
            Activity(
                id=uuid.uuid4(),
                type=ActivityType.meeting,
                direction=Direction.outbound,
                source="gcal",
                occurred_at=occurred,
                known_at=max(known, T0),
                cancelled_at=cancelled,
                extra={},
                participant_emails=[],
                contact_ids=[],
                match_method=MatchMethod.contact_email,
                match_confidence=1.0,
                injection_flag=False,
            )
        )
    h.activities.sort(key=lambda a: a.occurred_at)
    days = sorted(set(draw(st.lists(st.integers(0, 600), max_size=40))))
    h.usage = [
        UsageDaily(date=date(2025, 1, 1) + timedelta(days=d), active_users=5, key_actions=50, departments={})
        for d in days
    ]
    return h, next_steps


@settings(max_examples=200, deadline=None)
@given(histories(), offsets)
def test_snapshot_never_contains_the_future(
    data: tuple[AccountHistory, list[tuple[datetime, str]]], t_off: int
) -> None:
    history, next_steps = data
    t = _at(t_off)
    snap = history.as_of(t, lookback_days=10_000)
    assert snap is not None
    for a in snap.activities:
        assert a.known_at <= t
        source = next(x for x in history.activities if x.id == a.id)
        assert a.cancelled == (source.cancelled_at is not None and source.cancelled_at <= t)
    local_day = t.astimezone(history.tz).date()
    assert all(u.date < local_day for u in snap.usage)
    expected = [v for at, v in next_steps if at <= t]
    deal = snap.deals[0]
    assert deal.next_step == (expected[-1] if expected else "")
    if deal.next_step_since is not None:
        assert deal.next_step_since <= t


def test_entity_created_later_is_invisible() -> None:
    h = AccountHistory()
    acc_id = uuid.uuid4()
    h.account = Account(id=acc_id, name="Late", domains=[], created_in_source_at=_at(100))
    h.tz = ZoneInfo("UTC")
    h.fields.add(acc_id, "exists", _at(100), True)
    assert h.as_of(_at(99)) is None
    assert h.as_of(_at(100)) is not None


def test_naive_datetime_is_rejected() -> None:
    h = AccountHistory()
    h.account = Account(id=uuid.uuid4(), name="X", domains=[], created_in_source_at=_at(0))
    h.tz = ZoneInfo("UTC")
    try:
        h.as_of(datetime(2025, 6, 1))
    except ValueError:
        return
    raise AssertionError("expected ValueError for a naive datetime")
