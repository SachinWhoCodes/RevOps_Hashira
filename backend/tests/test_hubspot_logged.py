"""Emails and meetings logged in HubSpot: reader, normaliser, de-duplication, and signals end to end.

Payload shapes follow objects read from a live HubSpot portal on 2026-10-11 (addresses replaced
with reserved .test domains).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.bootstrap import ensure_org
from casefile.connectors.base import AuthError, RawItem
from casefile.connectors.http import HttpSource
from casefile.connectors.hubspot.reader import HubSpotReader
from casefile.db.models import (
    Account,
    Activity,
    ActivityType,
    Contact,
    Direction,
    MatchMethod,
    Org,
)
from casefile.identity.resolver import resolve_identities
from casefile.ingest.context import NormalizeContext
from casefile.ingest.normalize import normalize
from casefile.ingest.util import field_mapping
from casefile.signals.base import run_signals
from casefile.timeline.snapshot import AccountHistory, collapse_cross_source

BASE = "https://api.hubapi.com"
NOW = datetime.now(UTC)


def ts(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def email_obj(
    oid: str,
    days_ago: float,
    *,
    frm: str,
    to: str,
    direction: str = "EMAIL",
    company: str | None = "c1",
    contacts: tuple[str, ...] = ("p1",),
    **props: Any,
) -> dict[str, Any]:
    headers = {"from": {"email": frm}, "to": [{"email": to}], "cc": [], "bcc": []}
    return {
        "id": oid,
        "properties": {
            "hs_timestamp": ts(days_ago),
            "hs_createdate": ts(days_ago),
            "hs_email_direction": direction,
            "hs_email_from_email": frm,
            "hs_email_to_email": to,
            "hs_email_headers": json.dumps(headers),
            "hs_email_subject": props.pop("subject", "Renewal check-in"),
            "hs_email_text": props.pop("text", "Hi, checking in on the renewal."),
            "hs_email_thread_id": props.pop("thread", "t-1"),
            **props,
        },
        "associations": {
            **({"companies": {"results": [{"id": company}]}} if company else {}),
            "contacts": {"results": [{"id": c} for c in contacts]},
        },
    }


def meeting_obj(
    oid: str, days_ago: float, *, attendees: str, outcome: str | None = None, booked_days_ago: float = 40
) -> dict[str, Any]:
    props = {
        "hs_timestamp": ts(days_ago),
        "hs_createdate": ts(booked_days_ago),
        "hs_meeting_title": "Quarterly review",
        "hs_meeting_body": "Agenda: renewal",
        "hs_meeting_start_time": ts(days_ago),
        "hs_meeting_end_time": ts(days_ago - 1 / 24),
        "hs_all_attendee_emails": attendees,
        "hs_lastmodifieddate": ts(min(days_ago, booked_days_ago)),
    }
    if outcome:
        props["hs_meeting_outcome"] = outcome
    return {
        "id": oid,
        "properties": props,
        "associations": {"companies": {"results": [{"id": "c1"}]}, "contacts": {"results": []}},
    }


# ---------------------------------------------------------------- reader


def _mock_empty(*objects: str) -> None:
    for obj in objects:
        respx.get(f"{BASE}/crm/v3/objects/{obj}").mock(return_value=httpx.Response(200, json={"results": []}))


def _reader() -> HubSpotReader:
    return HubSpotReader("t", field_mapping(), http=HttpSource("hubspot", httpx.Client(base_url=BASE)))


@respx.mock
def test_reader_fetches_logged_emails_and_meetings_with_their_contacts() -> None:
    respx.get(f"{BASE}/crm/v3/owners").mock(return_value=httpx.Response(200, json={"results": []}))
    _mock_empty("companies", "contacts", "deals", "notes", "tasks", "calls")
    one_email = email_obj("e1", 3, frm="a@x.test", to="b@y.test")
    emails = respx.get(f"{BASE}/crm/v3/objects/emails").mock(
        return_value=httpx.Response(200, json={"results": [one_email]})
    )
    meetings = respx.get(f"{BASE}/crm/v3/objects/meetings").mock(
        return_value=httpx.Response(200, json={"results": [meeting_obj("m1", 3, attendees="b@y.test")]})
    )
    items = [i for page in _reader().pages({}) for i in page.items]
    assert [i.object_type for i in items] == ["hubspot.email", "hubspot.meeting"]
    for route in (emails, meetings):
        params = route.calls[0].request.url.params
        assert params["associations"] == "companies,contacts"
    assert "hs_email_message_id" in emails.calls[0].request.url.params["properties"]
    assert "hs_meeting_outcome" in meetings.calls[0].request.url.params["properties"]


@respx.mock
def test_missing_email_scope_skips_only_emails() -> None:
    respx.get(f"{BASE}/crm/v3/owners").mock(return_value=httpx.Response(200, json={"results": []}))
    _mock_empty("companies", "contacts", "deals", "notes", "tasks", "calls")
    respx.get(f"{BASE}/crm/v3/objects/emails").mock(return_value=httpx.Response(403, text="missing scope"))
    respx.get(f"{BASE}/crm/v3/objects/meetings").mock(
        return_value=httpx.Response(200, json={"results": [meeting_obj("m1", 3, attendees="b@y.test")]})
    )
    pages = list(_reader().pages({}))
    assert [i.object_type for p in pages for i in p.items] == ["hubspot.meeting"]
    final = pages[-1].cursor
    assert "403" in final["emails"]["skipped"] and "since" not in final["emails"]  # retried next run
    assert "since" in final["meetings"]


@respx.mock
def test_a_revoked_token_still_fails_the_sync() -> None:
    respx.get(f"{BASE}/crm/v3/owners").mock(return_value=httpx.Response(200, json={"results": []}))
    _mock_empty("companies", "contacts", "deals", "notes", "tasks", "calls")
    respx.get(f"{BASE}/crm/v3/objects/emails").mock(return_value=httpx.Response(401))
    with pytest.raises(AuthError):
        list(_reader().pages({}))


# ---------------------------------------------------------------- normaliser


@pytest.fixture
def crm(db: Session) -> dict[str, Any]:
    org = ensure_org(db, slug="hs-logged", name="Vendor", timezone="UTC", internal_domains=["vendor.test"])
    acc = Account(
        org_id=org.id,
        crm_source="hubspot",
        crm_id="c1",
        name="Acme",
        domains=["acme.test"],
        created_in_source_at=NOW - timedelta(days=500),
    )
    db.add(acc)
    db.flush()
    # Through the real CRM path, so the role arrives with its field history as it would live.
    contact = {
        "id": "p1",
        "createdAt": ts(500),
        "properties": {
            "email": "dana@acme.test",
            "firstname": "Dana",
            "hs_buying_role": "CHAMPION",
            "associatedcompanyid": "c1",
            "createdate": ts(500),
            "lastmodifieddate": ts(500),
        },
    }
    ingest(db, org, "contact", contact)
    dana = db.scalars(select(Contact).where(Contact.email == "dana@acme.test")).one()
    return {"org": org, "account": acc, "dana": dana}


def ingest(db: Session, org: Org, kind: str, *payloads: dict[str, Any]) -> None:
    ctx = NormalizeContext(session=db, org=org)
    normalize(ctx, [RawItem(f"hubspot.{kind}", p["id"], p) for p in payloads])
    db.flush()


def activity(db: Session, source_id: str) -> Activity:
    return db.scalars(select(Activity).where(Activity.source_id == source_id)).one()


def test_logged_email_becomes_an_email_activity(db: Session, crm: dict[str, Any]) -> None:
    out = email_obj(
        "e1",
        5,
        frm="owner@vendor.test",
        to="dana@acme.test",
        hs_email_message_id="<CAF123@mail.vendor.test>",
    )
    reply = email_obj("e2", 4, frm="dana@acme.test", to="owner@vendor.test", direction="INCOMING_EMAIL")
    ingest(db, crm["org"], "email", out, reply)
    a, b = activity(db, "email:e1"), activity(db, "email:e2")
    assert (a.type, a.direction, b.direction) == (ActivityType.email, Direction.outbound, Direction.inbound)
    assert a.participant_emails == ["owner@vendor.test", "dana@acme.test"]
    assert (a.account_id, a.match_method, a.match_confidence) == (
        crm["account"].id,
        MatchMethod.crm_association,
        0.95,
    )
    assert a.contact_ids == [crm["dana"].id]
    assert a.thread_id == b.thread_id == "hubspot:t-1"
    assert a.dedupe_key == "<CAF123@mail.vendor.test>" and a.body_purge_after is not None


def test_bounce_and_out_of_office_are_recognised(db: Session, crm: dict[str, Any]) -> None:
    bounced = email_obj("e1", 5, frm="owner@vendor.test", to="dana@acme.test", hs_email_status="BOUNCED")
    ooo = email_obj(
        "e2",
        4,
        frm="dana@acme.test",
        to="owner@vendor.test",
        direction="INCOMING_EMAIL",
        subject="Automatic reply: Renewal check-in",
        text="I have left Acme. Please contact Ravi.",
        hs_incoming_email_is_out_of_office="true",
    )
    ingest(db, crm["org"], "email", bounced, ooo)
    b, o = activity(db, "email:e1"), activity(db, "email:e2")
    assert b.extra["bounce"] and b.extra["failed_recipient"] == "dana@acme.test"
    assert o.extra["auto_reply"] and not o.extra["bounce"]


def test_meeting_outcomes_and_internal_meetings(db: Session, crm: dict[str, Any]) -> None:
    ingest(
        db,
        crm["org"],
        "meeting",
        meeting_obj("m1", 10, attendees="dana@acme.test;owner@vendor.test", outcome="COMPLETED"),
        meeting_obj("m2", 8, attendees="dana@acme.test", outcome="CANCELED"),
        meeting_obj("m3", 6, attendees="dana@acme.test", outcome="NO_SHOW"),
        meeting_obj("m4", 4, attendees="owner@vendor.test;cto@vendor.test"),
    )
    held, cancelled, no_show = (activity(db, f"meeting:{m}") for m in ("m1", "m2", "m3"))
    assert held.cancelled_at is None and held.type == ActivityType.meeting
    assert cancelled.cancelled_at is not None and no_show.cancelled_at is not None  # did not take place
    assert held.known_at < held.occurred_at  # booked 40 days ago: visible as scheduled before it happened
    assert not db.scalars(select(Activity).where(Activity.source_id == "meeting:m4")).all()  # internal


def test_unassociated_email_is_matched_by_address(db: Session, crm: dict[str, Any]) -> None:
    loose = email_obj("e9", 2, frm="owner@vendor.test", to="dana@acme.test", company=None, contacts=())
    ingest(db, crm["org"], "email", loose)
    assert activity(db, "email:e9").match_method == MatchMethod.unmatched
    resolve_identities(db, crm["org"])
    a = activity(db, "email:e9")
    assert (a.account_id, a.match_method) == (crm["account"].id, MatchMethod.contact_email)


def test_crm_token_alone_is_enough_for_relationship_signals(db: Session, crm: dict[str, Any]) -> None:
    """No Gmail, no Calendar: the meeting gap and champion silence fire from HubSpot-logged records."""
    ingest(
        db,
        crm["org"],
        "meeting",
        meeting_obj("m1", 30, attendees="dana@acme.test", outcome="COMPLETED", booked_days_ago=45),
    )
    ingest(
        db,
        crm["org"],
        "email",
        email_obj("e1", 20, frm="owner@vendor.test", to="dana@acme.test", thread="a"),
        email_obj("e2", 12, frm="owner@vendor.test", to="dana@acme.test", thread="b"),
    )
    snap = AccountHistory.load(db, crm["account"].id).as_of(NOW)
    assert snap is not None
    fired = {h.type: h for h in run_signals(snap)}
    assert fired["meeting_gap"].facts["days_since_last_meeting"] >= 29
    assert fired["champion_silence"].facts["unanswered_emails"] == 2


def test_logged_out_of_office_saying_they_left_flags_a_departure(db: Session, crm: dict[str, Any]) -> None:
    ooo = email_obj(
        "e1",
        3,
        frm="dana@acme.test",
        to="owner@vendor.test",
        direction="INCOMING_EMAIL",
        subject="Automatic reply: Renewal check-in",
        text="I have left Acme and this mailbox is no longer monitored.",
        hs_incoming_email_is_out_of_office="true",
    )
    ingest(db, crm["org"], "email", ooo)
    snap = AccountHistory.load(db, crm["account"].id).as_of(NOW)
    assert snap is not None
    (hit,) = [h for h in run_signals(snap) if h.type == "champion_departure"]
    assert hit.facts["how"] == ["auto-reply says they left"]


# ---------------------------------------------------------------- one copy per email or meeting


def _act(source: str, kind: ActivityType, minutes_ago: float, **kw: Any) -> Activity:
    return Activity(
        id=uuid.uuid4(),
        source=source,
        source_id=f"{source}:{uuid.uuid4()}",
        type=kind,
        occurred_at=NOW - timedelta(minutes=minutes_ago),
        participant_emails=kw.pop("participants", ["dana@acme.test"]),
        **kw,
    )


def test_the_same_email_from_gmail_and_hubspot_counts_once() -> None:
    gmail = _act("gmail", ActivityType.email, 60, dedupe_key="<abc@x.test>", from_email="o@vendor.test")
    logged = _act("hubspot", ActivityType.email, 59, dedupe_key="abc@x.test", from_email="o@vendor.test")
    kept = collapse_cross_source([logged, gmail])
    assert kept == [gmail]  # matched on Message-ID; the mailbox copy wins


def test_emails_without_message_id_match_on_sender_subject_and_minute() -> None:
    gmail = _act("gmail", ActivityType.email, 60, from_email="o@vendor.test", subject="Renewal")
    logged = _act("hubspot", ActivityType.email, 60, from_email="O@vendor.test", subject="RE: renewal")
    other = _act("hubspot", ActivityType.email, 30, from_email="o@vendor.test", subject="Renewal")
    assert collapse_cross_source([gmail, logged, other]) == [gmail, other]


def test_records_from_one_source_are_never_collapsed() -> None:
    a = _act("hubspot", ActivityType.email, 60, from_email="o@vendor.test", subject="Hi")
    b = _act("hubspot", ActivityType.email, 60, from_email="o@vendor.test", subject="Hi")
    assert len(collapse_cross_source([a, b])) == 2


def test_the_same_meeting_from_calendar_and_hubspot_counts_once() -> None:
    cal = _act("gcal", ActivityType.meeting, 600, participants=["o@vendor.test", "dana@acme.test"])
    logged = _act("hubspot", ActivityType.meeting, 600, participants=["dana@acme.test"])
    unrelated = _act("hubspot", ActivityType.meeting, 600, participants=["ravi@globex.test"])
    assert collapse_cross_source([logged, cal, unrelated]) == [cal, unrelated]
