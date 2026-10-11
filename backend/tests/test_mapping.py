"""C2: per-company field mapping — validation, versioned saves, re-applying to stored data, the API."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.api.deps import get_db
from casefile.api.main import app
from casefile.bootstrap import ensure_connection, ensure_org
from casefile.connectors.base import RawItem
from casefile.db.models import AuditLog, ConfigVersion, ConnectionKind, Contact
from casefile.ingest.context import NormalizeContext
from casefile.ingest.mapping import MappingError, defaults, org_mapping, reapply, save, validate
from casefile.ingest.normalize import normalize
from casefile.ingest.sync_runner import store_raw
from casefile.timeline.snapshot import AccountHistory

CSRF = {"x-casefile-csrf": "1"}
NOW = datetime.now(UTC)


def edited(**changes: Any) -> dict[str, Any]:
    m = defaults()
    for path, value in changes.items():
        node = m["hubspot"]
        *parents, leaf = path.split("__")
        for p in parents:
            node = node[p]
        node[leaf] = value
    return m


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"company__seats_purchased": "Seats Purchased!"}, "not a CRM property name"),
        ({"contact__role_values": {"CHAMPION": "hero"}}, "allowed"),
        ({"deal__won_stages": ["closedwon"], "deal__lost_stages": ["closedwon"]}, "both won and lost"),
        ({"deal__type_values": {"newbusiness": "new"}}, "renewal"),
        ({"engagement_types": ["notes", "faxes"]}, "engagement types"),
        ({"deal__won_stages": []}, "at least one stage"),
    ],
)
def test_invalid_mappings_are_refused(change: dict[str, Any], message: str) -> None:
    with pytest.raises(MappingError, match=message):
        validate(edited(**change))


def test_defaults_are_valid_and_a_crm_without_champion_values_is_allowed() -> None:
    assert validate(defaults())["hubspot"] == defaults()["hubspot"]
    assert validate(edited(contact__role_values={"BUDGET_HOLDER": "economic_buyer"}))


def test_saves_are_versioned_audited_and_unchanged_saves_are_free(db: Session) -> None:
    org = ensure_org(db, slug="map-org", name="Vendor", timezone="UTC", internal_domains=["vendor.test"])
    assert save(db, org.id, defaults(), None) == 0  # identical to the defaults: nothing stored
    m = edited(contact__role_values={"SPONSOR": "champion", "BUDGET_HOLDER": "economic_buyer"})
    assert save(db, org.id, m, None) == 1
    assert save(db, org.id, m, None) == 1  # unchanged
    assert save(db, org.id, edited(company__arr="annual_value"), None) == 2
    rows = db.scalars(select(ConfigVersion).where(ConfigVersion.org_id == org.id)).all()
    assert [r.version for r in rows] == [1, 2]  # history kept, never edited
    assert org_mapping(db, org.id)["hubspot"]["company"]["arr"] == "annual_value"
    audit = db.scalars(
        select(AuditLog).where(AuditLog.org_id == org.id, AuditLog.action == "config_changed")
    ).all()
    assert [a.details["changed"] for a in audit] == [
        ["contact.role_values"],
        ["company.arr", "contact.role_values"],
    ]


def test_reapply_rebuilds_stored_data_with_the_new_meaning(db: Session) -> None:
    org = ensure_org(db, slug="map-reapply", name="Vendor", timezone="UTC", internal_domains=["vendor.test"])
    conn = ensure_connection(db, org, ConnectionKind.hubspot, name="crm")
    old = (NOW - timedelta(days=300)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    company = {
        "id": "c1",
        "createdAt": old,
        "properties": {"name": "Acme", "domain": "acme.test", "createdate": old},
    }
    contact = {
        "id": "p1",
        "createdAt": old,
        "properties": {
            "email": "dana@acme.test",
            "firstname": "Dana",
            "hs_buying_role": "SPONSOR",
            "associatedcompanyid": "c1",
            "createdate": old,
            "lastmodifieddate": old,
        },
    }
    items = [RawItem("hubspot.company", "c1", company), RawItem("hubspot.contact", "p1", contact)]
    normalize(NormalizeContext(session=db, org=org), store_raw(db, conn, items))
    db.flush()
    dana = db.scalars(select(Contact).where(Contact.email == "dana@acme.test")).one()

    def role() -> str:
        snap = AccountHistory.load(db, dana.account_id).as_of(NOW)  # type: ignore[arg-type]
        assert snap is not None
        return next(c.role for c in snap.contacts if c.email == "dana@acme.test")

    assert role() == "unknown"  # the default mapping doesn't know "SPONSOR"
    save(
        db,
        org.id,
        edited(
            contact__role_values={**defaults()["hubspot"]["contact"]["role_values"], "SPONSOR": "champion"}
        ),
        None,
    )
    out = reapply(db, org.id)
    assert out["raw_records"] == 2
    db.refresh(dana)
    assert role() == "champion" and dana.role.value == "champion"
    assert db.scalars(
        select(AuditLog).where(AuditLog.org_id == org.id, AuditLog.action == "mapping_reapplied")
    ).one()


# ---------------------------------------------------------------- API


@pytest.fixture(scope="module")
def client(synced: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: synced
    with TestClient(app) as c:
        c.post(
            "/api/auth/login",
            json={"email": "admin@lumora.test", "password": "test-password-1"},
            headers=CSRF,
        )
        yield c
    app.dependency_overrides.clear()


def test_mapping_api(client: TestClient) -> None:
    got = client.get("/api/admin/field-mapping").json()
    assert got["version"] == 0 and got["live_crm"] is False
    bad = copy.deepcopy(got["mapping"])
    bad["hubspot"]["deal"]["won_stages"] = []
    assert client.put("/api/admin/field-mapping", json=bad, headers=CSRF).status_code == 422
    check = client.post("/api/admin/field-mapping/check", json=got["mapping"], headers=CSRF).json()
    assert check["ok"] and check["checked_against_crm"] is False  # no live CRM in tests
    good = copy.deepcopy(got["mapping"])
    good["hubspot"]["contact"]["role_values"]["SPONSOR"] = "champion"
    saved = client.put("/api/admin/field-mapping", json=good, headers=CSRF).json()
    assert saved["version"] == 1 and saved["reapply_needed"]
    assert client.post("/api/admin/field-mapping/reapply", json={}, headers=CSRF).json() == {"queued": True}
