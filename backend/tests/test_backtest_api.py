"""Back-test API: run, results with n and ranges, label correction, replay."""

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
        assert (
            c.post(
                "/api/auth/login",
                json={"email": "admin@lumora.test", "password": "test-password-1"},
                headers=CSRF,
            ).status_code
            == 200
        )
        yield c
    app.dependency_overrides.clear()


def test_run_results_relabel_and_replay(client: TestClient, synced: Session) -> None:
    run = client.post("/api/backtest/run", headers=CSRF).json()
    assert run["label"] == "simulated" and run["metrics"]["events"]["scored"] > 0
    assert client.get("/api/backtest/latest").json()["id"] == run["id"]

    rows = client.get("/api/backtest/outcomes").json()
    assert rows and {"outcome", "label_source", "risk_before"} <= set(rows[0])
    target = rows[0]
    new = "late" if target["outcome"] != "late" else "renewed"
    r = client.patch(
        f"/api/backtest/outcomes/{target['id']}", json={"outcome": new, "note": "checked"}, headers=CSRF
    )
    assert r.status_code == 200 and r.json()["label_source"] == "human"

    org = synced.scalars(select(Org).where(Org.slug == "test-org")).one()
    acme = synced.scalars(select(Account).where(Account.name == "Acme Corp", Account.org_id == org.id)).one()
    rep = client.get(f"/api/backtest/replay/{acme.id}?weeks=20").json()
    assert rep["points"] and rep["flag_at"] == 40 and "negative_tone" in rep["excluded_signals"]
    day = rep["points"][-1]["date"]
    said = client.get(f"/api/backtest/replay/{acme.id}/explain?as_of={day}").json()
    assert said["as_of"] == day and "template" in said["source"]
