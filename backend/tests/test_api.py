from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.api.deps import get_db
from casefile.api.main import app
from casefile.db.models import Account, Org

CSRF = {"x-casefile-csrf": "1"}


@pytest.fixture(scope="module")
def client(synced: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: synced
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _login(client: TestClient, password: str = "test-password-1") -> int:
    return client.post(
        "/api/auth/login", json={"email": "admin@lumora.test", "password": password}, headers=CSRF
    ).status_code


def test_requires_login(client: TestClient) -> None:
    client.cookies.clear()
    assert client.get("/api/accounts").status_code == 401


def test_wrong_password_rejected(client: TestClient) -> None:
    assert _login(client, "nope") == 401


def test_writes_need_csrf_header(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"email": "admin@lumora.test", "password": "test-password-1"})
    assert r.status_code == 403


def test_accounts_and_timeline(client: TestClient, synced: Session) -> None:
    assert _login(client) == 200
    accounts = client.get("/api/accounts").json()
    acme = next(a for a in accounts if a["name"] == "Acme Corp")
    assert acme["next_meeting_at"] is None
    timeline = client.get(f"/api/accounts/{acme['id']}").json()
    assert timeline["exists"] and timeline["account"]["name"] == "Acme Corp"
    assert {"gmail", "gcal", "usage"} <= set(timeline["sources"])
    past = client.get(f"/api/accounts/{acme['id']}?as_of=2026-04-01").json()
    assert all(e["at"] <= "2026-04-02" or e.get("scheduled") for e in past["events"])
    assert len(past["events"]) < len(timeline["events"]) or past["event_count"] < timeline["event_count"]


def test_other_org_account_is_not_visible(client: TestClient, synced: Session) -> None:
    assert _login(client) == 200
    other = Org(slug="other-org", name="Other", timezone="UTC", internal_domains=[], settings={})
    synced.add(other)
    synced.flush()
    acc = Account(org_id=other.id, crm_source="hubspot", crm_id="x1", name="Hidden", domains=[])
    synced.add(acc)
    synced.flush()
    assert client.get(f"/api/accounts/{acc.id}").status_code == 404
    names = [a["name"] for a in client.get("/api/accounts").json()]
    assert "Hidden" not in names


def test_unmatched_and_connections(client: TestClient, synced: Session) -> None:
    assert _login(client) == 200
    data = client.get("/api/identity/unmatched").json()
    assert all("@lumora.test" not in e for a in data["activities"] for e in a["external"])
    conns = client.get("/api/connections").json()
    assert conns and conns[0]["freshness"]["state"] in {"fresh", "stale"}
    org = synced.scalars(select(Org).where(Org.slug == "test-org")).one()
    assert org is not None
