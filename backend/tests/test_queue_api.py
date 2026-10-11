from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.api.deps import get_db
from casefile.api.main import app
from casefile.cases.service import recompute_all
from casefile.db.models import Org
from tests.conftest import TEST_TODAY

CSRF = {"x-casefile-csrf": "1"}


@pytest.fixture(scope="module")
def client(synced: Session) -> Iterator[TestClient]:
    org = synced.scalars(select(Org).where(Org.slug == "test-org")).one()
    recompute_all(
        synced, org, as_of=datetime(TEST_TODAY.year, TEST_TODAY.month, TEST_TODAY.day, 12, tzinfo=UTC)
    )
    app.dependency_overrides[get_db] = lambda: synced
    with TestClient(app) as c:
        r = c.post(
            "/api/auth/login",
            json={"email": "admin@lumora.test", "password": "test-password-1"},
            headers=CSRF,
        )
        assert r.status_code == 200
        yield c
    app.dependency_overrides.clear()


def test_queue_ranks_by_priority_with_breakdowns(client: TestClient) -> None:
    q = client.get("/api/queue?kind=risk").json()
    assert q["items"], "expected at least one open risk case"
    priorities = [i["priority"] for i in q["items"]]
    assert priorities == sorted(priorities, reverse=True)
    assert [i["rank"] for i in q["items"]] == list(range(1, len(q["items"]) + 1))
    acme = next(i for i in q["items"] if i["account"]["name"] == "Acme Corp")
    assert sum(line["points"] for line in acme["risk_lines"]) == acme["risk"]
    assert all(s["evidence"] for s in acme["signals"])


def test_owner_filter(client: TestClient) -> None:
    q = client.get("/api/queue?kind=risk").json()
    owner = next(o for o in q["owners"] if o["name"] == "Priya Sharma")
    mine = client.get(f"/api/queue?kind=risk&owner={owner['id']}").json()["items"]
    assert mine and all(i["owner"] == "Priya Sharma" for i in mine)
    assert client.get("/api/queue?kind=risk&owner=me").json()["items"] == []  # admin owns no accounts


def test_timeline_includes_live_assessment_and_time_machine(client: TestClient) -> None:
    acme_id = next(
        i for i in client.get("/api/queue?kind=risk").json()["items"] if i["account"]["name"] == "Acme Corp"
    )["account"]["id"]
    now = client.get(f"/api/accounts/{acme_id}").json()
    assert now["assessment"]["risk"]["score"] >= 40
    assert any(c["kind"] == "risk" and c["status"] == "open" for c in now["cases"])
    before = client.get(f"/api/accounts/{acme_id}?as_of=2026-06-01").json()
    assert before["assessment"]["risk"]["score"] < now["assessment"]["risk"]["score"]


def test_blind_spots_config_and_audit(client: TestClient) -> None:
    spots = client.get("/api/blind-spots").json()
    assert spots["accounts_assessed"] > 0 and "by_code" in spots
    cfg = client.get("/api/config").json()
    assert {s["name"] for s in cfg["signals"]} >= {"meeting_gap", "champion_silence", "usage_decline"}
    assert cfg["editable"] is False
    assert client.get("/api/audit/verify").json()["ok"] is True
    assert client.get("/api/audit?limit=5").json()


def test_audit_pages_by_sequence_without_gaps_or_overlaps(client: TestClient) -> None:
    whole = [r["seq"] for r in client.get("/api/audit?limit=12").json()]
    assert len(whole) == 12 and whole == sorted(whole, reverse=True)
    first = [r["seq"] for r in client.get("/api/audit?limit=5").json()]
    second = [r["seq"] for r in client.get(f"/api/audit?limit=5&before_seq={first[-1]}").json()]
    third = [r["seq"] for r in client.get(f"/api/audit?limit=2&before_seq={second[-1]}").json()]
    assert first + second + third == whole  # newest first, each page continues where the last stopped
    assert client.get("/api/audit?before_seq=0").status_code == 422


def test_recompute_requires_admin_and_csrf(client: TestClient) -> None:
    assert client.post("/api/admin/recompute").status_code == 403  # no CSRF header
    assert client.post("/api/admin/recompute", headers=CSRF).json()["queued"] > 0
