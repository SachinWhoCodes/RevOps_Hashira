"""Sandbox read-back: changes made in the HubSpot sandbox on mirrored deals flow back into scoring.

Response shapes follow a live read of the sandbox portal on 2026-10-11.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.bootstrap import ensure_org
from casefile.connectors.base import RawItem
from casefile.connectors.http import HttpSource
from casefile.connectors.hubspot.mirror import MirroredDeal, SandboxMirrorReader
from casefile.db.models import Activity, Deal, FieldHistory, Org
from casefile.ingest.context import NormalizeContext
from casefile.ingest.normalize import normalize
from casefile.signals.base import run_signals
from casefile.timeline.snapshot import AccountHistory

BASE = "https://api.hubapi.com"
NOW = datetime.now(UTC)
COPIED = NOW - timedelta(hours=3)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def portal_deal(pid: str, *, next_step_history: list[tuple[datetime, str]]) -> dict[str, Any]:
    return {
        "id": pid,
        "createdAt": iso(COPIED),
        "properties": {"dealname": "Acme Corp — Renewal 2026", "hs_next_step": next_step_history[-1][1]},
        "propertiesWithHistory": {
            "hs_next_step": [
                {"value": v, "timestamp": iso(at), "sourceType": "INTEGRATION"}
                for at, v in reversed(next_step_history)
            ],
            "dealstage": [{"value": "qualifiedtobuy", "timestamp": iso(COPIED), "sourceType": "INTEGRATION"}],
        },
    }


# ---------------------------------------------------------------- reader scope


@respx.mock  # any request not mocked below fails the test
def test_reader_only_asks_about_mirrored_deals() -> None:
    deals = respx.post(f"{BASE}/crm/v3/objects/deals/batch/read").mock(
        return_value=httpx.Response(
            200, json={"results": [portal_deal("900", next_step_history=[(COPIED, "")])]}
        )
    )
    for kind in ("tasks", "notes", "emails", "meetings"):
        respx.get(f"{BASE}/crm/v4/objects/deals/900/associations/{kind}").mock(
            return_value=httpx.Response(
                200, json={"results": [{"toObjectId": 77}] if kind == "tasks" else []}
            )
        )
    tasks = respx.post(f"{BASE}/crm/v3/objects/tasks/batch/read").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "77",
                        "properties": {"hs_task_subject": "Re-engage Dana", "hs_timestamp": iso(NOW)},
                    }
                ]
            },
        )
    )
    reader = SandboxMirrorReader(
        "t", [MirroredDeal("900", "20001", "10001")], http=HttpSource("hs", httpx.Client(base_url=BASE))
    )
    items = [i for page in reader.pages({}) for i in page.items]
    assert [i.object_type for i in items] == ["sandbox.deal", "hubspot.task"]
    assert json.loads(deals.calls[0].request.content)["inputs"] == [{"id": "900"}]
    assert json.loads(tasks.calls[0].request.content)["inputs"] == [{"id": "77"}]
    task = items[1].payload
    # Re-pointed at our account; tagged so the timeline says where it came from.
    assert task["associations"] == {"companies": {"results": [{"id": "10001"}]}}
    assert task["_casefile"] == {"via": "sandbox_mirror"} and task["_object"] == "task"


def test_no_mirrored_deals_means_no_requests() -> None:
    reader = SandboxMirrorReader("t", [], http=HttpSource("hs", httpx.Client(base_url=BASE)))
    assert [i for page in reader.pages({}) for i in page.items] == []


# ---------------------------------------------------------------- normaliser and signals


@pytest.fixture
def org_with_renewal(db: Session) -> tuple[Org, Deal]:
    org = ensure_org(db, slug="readback", name="Vendor", timezone="UTC", internal_domains=["vendor.test"])
    old = iso(NOW - timedelta(days=400))
    company = {
        "id": "10001",
        "createdAt": old,
        "properties": {"name": "Acme Corp", "domain": "acme.test", "createdate": old},
    }
    deal = {
        "id": "20001",
        "createdAt": old,
        "properties": {
            "dealname": "Acme Corp — Renewal 2026",
            "dealtype": "existingbusiness",
            "dealstage": "qualifiedtobuy",
            "amount": "100000",
            "closedate": iso(NOW + timedelta(days=43)),
            "hs_next_step": "",
            "createdate": old,
        },
        "associations": {"companies": {"results": [{"id": "10001"}]}},
    }
    ctx = NormalizeContext(session=db, org=org)
    normalize(ctx, [RawItem("hubspot.company", "10001", company), RawItem("hubspot.deal", "20001", deal)])
    db.flush()
    return org, db.scalars(select(Deal).where(Deal.crm_id == "20001")).one()


def read_back(db: Session, org: Org, *items: RawItem) -> set[Any]:
    ctx = NormalizeContext(session=db, org=org)
    normalize(ctx, list(items))
    db.flush()
    return ctx.touched_accounts


def fires(db: Session, deal: Deal) -> set[str]:
    snap = AccountHistory.load(db, deal.account_id).as_of(NOW)  # type: ignore[arg-type]
    assert snap is not None
    return {h.type for h in run_signals(snap)}


def test_executed_next_step_comes_back_and_clears_the_signal(
    db: Session, org_with_renewal: tuple[Org, Deal]
) -> None:
    org, deal = org_with_renewal
    assert "missing_next_step" in fires(db, deal)
    written = NOW - timedelta(hours=2)
    payload = portal_deal("900", next_step_history=[(COPIED, ""), (written, "Renewal check-in with Dana")])
    payload["_casefile"] = {"via": "sandbox_mirror", "local_deal_crm_id": "20001"}
    touched = read_back(db, org, RawItem("sandbox.deal", "900", payload))
    assert touched == {deal.account_id}
    rows = db.scalars(
        select(FieldHistory).where(
            FieldHistory.entity_id == deal.id, FieldHistory.source == "hubspot_sandbox"
        )
    ).all()
    # Only the change made after the copy; the copy's own initial values are not news.
    assert [(r.field, r.value) for r in rows] == [("next_step", "Renewal check-in with Dana")]
    assert deal.next_step == "Renewal check-in with Dana"
    assert "missing_next_step" not in fires(db, deal)


def test_the_copy_echoing_our_own_values_changes_nothing(
    db: Session, org_with_renewal: tuple[Org, Deal]
) -> None:
    org, deal = org_with_renewal
    payload = portal_deal("900", next_step_history=[(COPIED, "Copied from Casefile")])
    payload["_casefile"] = {"via": "sandbox_mirror", "local_deal_crm_id": "20001"}
    assert read_back(db, org, RawItem("sandbox.deal", "900", payload)) == set()
    assert "missing_next_step" in fires(db, deal)


def test_records_attached_in_the_portal_land_on_our_account(
    db: Session, org_with_renewal: tuple[Org, Deal]
) -> None:
    org, deal = org_with_renewal
    task = {
        "id": "406000000077",
        "_object": "task",
        "properties": {
            "hs_task_subject": "Re-engage Dana",
            "hs_task_status": "COMPLETED",
            "hs_timestamp": iso(NOW),
        },
        "associations": {"companies": {"results": [{"id": "10001"}]}},
        "_casefile": {"via": "sandbox_mirror"},
    }
    read_back(db, org, RawItem("hubspot.task", task["id"], task))
    row = db.scalars(select(Activity).where(Activity.source_id == "task:406000000077")).one()
    assert row.account_id == deal.account_id
    assert row.extra == {"crm_object": "task", "via": "sandbox_mirror", "status": "COMPLETED"}
