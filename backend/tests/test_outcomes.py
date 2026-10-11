"""Phase 6.1 outcome tracker: checkpoints after an executed play, measured through as-of snapshots."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.bootstrap import ensure_org
from casefile.connectors.base import RawItem
from casefile.db.models import (
    AccountCase,
    Activity,
    ActivityType,
    AuditLog,
    CaseKind,
    ChangeItem,
    ChangeItemStatus,
    ChangeSet,
    Deal,
    Direction,
    ExecutionAttempt,
    MatchMethod,
    PlayOutcome,
)
from casefile.ingest.context import NormalizeContext
from casefile.ingest.normalize import normalize
from casefile.outcomes.tracker import account_outcomes, classify, plays_table, track

NOW = datetime.now(UTC).replace(microsecond=0)
PLAYED = NOW - timedelta(days=10)  # the change set was executed ten days ago
DANA = "dana@acme.test"


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


@pytest.fixture
def played(db: Session) -> dict[str, Any]:
    org = ensure_org(db, slug="outcomes", name="Vendor", timezone="UTC", internal_domains=["vendor.test"])
    old = iso(NOW - timedelta(days=400))
    normalize(
        NormalizeContext(session=db, org=org),
        [
            RawItem(
                "hubspot.company",
                "c1",
                {
                    "id": "c1",
                    "createdAt": old,
                    "properties": {"name": "Acme", "domain": "acme.test", "createdate": old},
                },
            ),
            RawItem(
                "hubspot.contact",
                "p1",
                {
                    "id": "p1",
                    "createdAt": old,
                    "properties": {
                        "email": DANA,
                        "firstname": "Dana",
                        "hs_buying_role": "CHAMPION",
                        "associatedcompanyid": "c1",
                        "createdate": old,
                        "lastmodifieddate": old,
                    },
                },
            ),
            RawItem(
                "hubspot.deal",
                "d1",
                {
                    "id": "d1",
                    "createdAt": old,
                    "properties": {
                        "dealname": "Acme renewal",
                        "dealtype": "existingbusiness",
                        "dealstage": "qualifiedtobuy",
                        "amount": "100000",
                        "closedate": iso(NOW + timedelta(days=40)),
                        "hs_next_step": "Renewal call",
                        "createdate": old,
                    },
                    "associations": {"companies": {"results": [{"id": "c1"}]}},
                },
            ),
        ],
    )
    db.flush()
    account_id = db.scalars(select(Deal.account_id).where(Deal.crm_id == "d1")).one()
    assert account_id is not None

    def act(kind: ActivityType, at: datetime, *, known: datetime | None = None, **kw: Any) -> None:
        db.add(
            Activity(
                org_id=org.id,
                account_id=account_id,
                type=kind,
                occurred_at=at,
                known_at=known or at,
                source="gmail" if kind == ActivityType.email else "gcal",
                source_id=str(uuid.uuid4()),
                match_method=MatchMethod.contact_email,
                match_confidence=1.0,
                extra=kw.pop("extra", {}),
                **kw,
            )
        )

    # Before the play: last meeting 50 days earlier, two unanswered emails to the champion.
    act(
        ActivityType.meeting,
        PLAYED - timedelta(days=50),
        participant_emails=[DANA],
        direction=Direction.outbound,
    )
    for days in (20, 12):
        act(
            ActivityType.email,
            PLAYED - timedelta(days=days),
            direction=Direction.outbound,
            from_email="owner@vendor.test",
            participant_emails=["owner@vendor.test", DANA],
        )
    # After it: Dana replies (day 2), a meeting is booked on day 3 for day 5 and held.
    act(
        ActivityType.email,
        PLAYED + timedelta(days=2),
        direction=Direction.inbound,
        from_email=DANA,
        participant_emails=[DANA, "owner@vendor.test"],
    )
    act(
        ActivityType.meeting,
        PLAYED + timedelta(days=5),
        known=PLAYED + timedelta(days=3),
        participant_emails=[DANA],
        direction=Direction.outbound,
    )
    # After the 7-day checkpoint: Dana's auto-reply says she has left. Day 7 must not see it.
    act(
        ActivityType.email,
        PLAYED + timedelta(days=8),
        direction=Direction.inbound,
        from_email=DANA,
        participant_emails=[DANA],
        subject="Automatic reply",
        snippet="I have left Acme.",
        extra={"auto_reply": True},
    )

    case = AccountCase(org_id=org.id, account_id=account_id, kind=CaseKind.risk)
    db.add(case)
    db.flush()
    cs = ChangeSet(
        org_id=org.id, case_id=case.id, playbook_id="champion_silent_renewal_near", playbook_version="v1"
    )
    db.add(cs)
    db.flush()
    item = ChangeItem(
        org_id=org.id,
        change_set_id=cs.id,
        seq=1,
        action_type="crm_task",
        target_system="hubspot",
        tier=1,
        status=ChangeItemStatus.succeeded,
        idempotency_key=f"t-{cs.id}",
    )
    db.add(item)
    db.flush()
    db.add(
        ExecutionAttempt(
            org_id=org.id,
            change_item_id=item.id,
            attempt_no=1,
            status="succeeded",
            started_at=PLAYED,
            finished_at=PLAYED + timedelta(minutes=1),
        )
    )
    db.flush()
    return {"org": org, "account_id": account_id, "change_set": cs}


def test_checkpoints_are_recorded_and_the_due_one_measured(db: Session, played: dict[str, Any]) -> None:
    org, cs = played["org"], played["change_set"]
    assert track(db, org, now=NOW) == {"sets": 1, "measured": 1, "pending": 2}
    rows = {r.check_day: r for r in db.scalars(select(PlayOutcome).where(PlayOutcome.change_set_id == cs.id))}
    assert set(rows) == {7, 14, 30}
    assert rows[14].result == rows[30].result == "pending" and rows[14].measured_at is None
    d7 = rows[7]
    events = d7.signals_after["events"]
    assert events["champion_replied"] and events["meeting_booked"] and events["meeting_held"]
    assert {"champion_silence", "meeting_gap"} <= set(d7.signals_after["cleared"])
    assert d7.signals_after["risk"] < d7.signals_before["risk"] and d7.result == "improved"
    # Measured as of the checkpoint: the departure on day 8 is not in the day-7 picture.
    assert "champion_departure" not in d7.signals_after["signals"]
    audit = db.scalars(select(AuditLog).where(AuditLog.action == "outcome_measured")).one()
    assert audit.details["check_day"] == 7 and audit.details["result"] == "improved"


def test_tracking_again_changes_nothing(db: Session, played: dict[str, Any]) -> None:
    org = played["org"]
    track(db, org, now=NOW)
    first = {(r.check_day, r.result, r.measured_at) for r in db.scalars(select(PlayOutcome))}
    assert track(db, org, now=NOW)["measured"] == 0
    assert {(r.check_day, r.result, r.measured_at) for r in db.scalars(select(PlayOutcome))} == first
    assert db.scalar(select(func.count()).select_from(PlayOutcome)) == 3


def test_a_late_run_measures_the_same_as_an_on_time_run(db: Session, played: dict[str, Any]) -> None:
    org, cs = played["org"], played["change_set"]
    track(db, org, now=NOW + timedelta(days=60))  # everything is due; day 7 is still "as of day 7"
    rows = {r.check_day: r for r in db.scalars(select(PlayOutcome).where(PlayOutcome.change_set_id == cs.id))}
    assert "champion_departure" not in rows[7].signals_after["signals"]
    assert "champion_departure" in rows[14].signals_after["signals"]  # day 14 does see it


def test_account_view_and_plays_table(db: Session, played: dict[str, Any]) -> None:
    org = played["org"]
    track(db, org, now=NOW)
    (view,) = account_outcomes(db, played["account_id"], now=NOW)
    assert view["play"] == "champion_silent_renewal_near" and view["days_since"] == 9
    assert [c["result"] for c in view["checkpoints"]] == ["improved", "pending", "pending"]
    assert view["so_far"]["after"]["events"]["champion_replied"]  # live reading, not stored
    (play,) = plays_table(db, org)
    d7, d14, _ = play["checkpoints"]
    assert play["sets"] == 1 and d7["measured"] == 1 and d7["improved"] == 1 and d7["champion_replied"] == 1
    assert d14["pending"] == 1 and d14["median_risk_change"] is None


def test_expansion_plays_are_judged_by_the_conversation() -> None:
    before, after = {"risk": 0}, {"risk": 0}
    assert (
        classify("expansion", before, after, {"meeting_booked": True, "buyer_engaged": False}) == "improved"
    )
    assert (
        classify("expansion", before, after, {"meeting_booked": False, "buyer_engaged": False}) == "no_change"
    )
    assert classify("risk", {"risk": 60}, {"risk": 55}, {}) == "no_change"  # under one signal's worth
    assert classify("risk", {"risk": 60}, {"risk": 75}, {}) == "worse"
