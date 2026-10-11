"""Approval inbox API, morning digest, executor status and audit export."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.ai.explain import explain_assessment, open_case_assessments
from casefile.api.deps import get_db
from casefile.api.main import app
from casefile.api.routers import changes
from casefile.bootstrap import ensure_connection
from casefile.cases.service import recompute_all
from casefile.db.models import ConnectionKind, Org, Zone
from casefile.executor.digest import send_digests
from tests.conftest import TEST_TODAY

CSRF = {"x-casefile-csrf": "1"}
NOW = datetime(TEST_TODAY.year, TEST_TODAY.month, TEST_TODAY.day, 12, tzinfo=UTC)


@pytest.fixture(scope="module")
def client(synced: Session) -> Iterator[TestClient]:
    org = synced.scalars(select(Org).where(Org.slug == "test-org")).one()
    recompute_all(synced, org, as_of=NOW)
    for aid in open_case_assessments(synced, org):
        explain_assessment(synced, aid, None)
    ensure_connection(
        synced,
        org,
        ConnectionKind.hubspot,
        name="t",
        zone=Zone.write,
        config={"id_source": "crm_id", "portal_id": "42"},
    )
    ensure_connection(
        synced, org, ConnectionKind.slack, name="t", zone=Zone.write, config={"fallback_user_id": "U0"}
    )
    ensure_connection(
        synced, org, ConnectionKind.gmail, name="t", zone=Zone.write, config={"mailbox": "me@lumora.test"}
    )
    synced.commit()
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


@pytest.fixture(autouse=True)
def no_live_crm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(changes, "_live", lambda: None)
    monkeypatch.setattr(changes, "AiGateway", lambda db, org_id: None)  # template drafts, no AI


def _acme_case(client: TestClient) -> str:
    q = client.get("/api/queue?kind=risk").json()
    return next(i for i in q["items"] if i["account"]["name"] == "Acme Corp")["case_id"]


def test_plan_approve_and_inbox(client: TestClient) -> None:
    case_id = _acme_case(client)
    plan = client.post(f"/api/cases/{case_id}/plan?force=true", headers=CSRF).json()
    assert plan["can_decide"] and plan["items"]
    task = next(i for i in plan["items"] if i["action_type"] == "crm_task")
    assert task["links"]["company"].startswith("https://app.hubspot.com/contacts/42/record/0-2/")
    assert any(i["action_type"] == "crm_task" for i in plan["items"])
    inbox = client.get("/api/inbox").json()
    assert any(s["id"] == plan["id"] for s in inbox)

    r = client.post(
        f"/api/change-items/{task['id']}/approve", json={"edits": {"subject": "Call Dana"}}, headers=CSRF
    )
    assert r.status_code == 200, r.text
    after = next(i for i in r.json()["items"] if i["id"] == task["id"])
    assert after["status"] == "queued" and after["final"]["subject"] == "Call Dana"
    assert after["approvals"][0]["edited"] is True
    again = client.post(f"/api/change-items/{task['id']}/approve", headers=CSRF)
    assert again.status_code == 409

    draft = next(i for i in plan["items"] if i["action_type"] == "gmail_draft")
    bad = client.post(
        f"/api/change-items/{draft['id']}/approve", json={"edits": {"to": ["x@evil.example"]}}, headers=CSRF
    )
    assert bad.status_code == 422

    tier1 = client.post(f"/api/change-sets/{plan['id']}/approve-tier1", headers=CSRF).json()
    assert next(i for i in tier1["items"] if i["id"] == draft["id"])["status"] == "proposed"


def test_get_plan_and_csrf(client: TestClient) -> None:
    case_id = _acme_case(client)
    assert client.get(f"/api/cases/{case_id}/plan").json()["case_id"] == case_id
    assert client.post(f"/api/cases/{case_id}/plan").status_code == 403  # missing CSRF header


def test_executor_status_and_audit_export(client: TestClient) -> None:
    status = client.get("/api/executor/status").json()
    assert status["running"] is False and {t["kind"] for t in status["targets"]} >= {"hubspot", "slack"}
    rows = client.get("/api/audit?action=change_item").json()
    assert rows and all(r["action"].startswith("change_item") for r in rows)
    csv = client.get("/api/audit?action=change_set&format=csv")
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0].startswith("seq,ts,actor,action")
    assert client.get("/api/audit/verify").json()["ok"] is True


class FakeSlack:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.blocks: list[list] = []

    def post_text(self, user_id: str, text: str, blocks: list | None = None) -> str:
        self.sent.append((user_id, text))
        self.blocks.append(blocks or [])
        return f"{len(self.sent)}.0"


def test_digest_once_per_owner_per_day(client: TestClient, synced: Session) -> None:
    org = synced.scalars(select(Org).where(Org.slug == "test-org")).one()
    slack = FakeSlack()
    early = datetime(2026, 10, 10, 2, 0, tzinfo=UTC)  # 07:30 in Asia/Kolkata: before 09:00
    assert send_digests(synced, org, slack, base_url="http://x", now=early) == []
    late = datetime(2026, 10, 10, 4, 0, tzinfo=UTC)  # 09:30 local
    sent = send_digests(synced, org, slack, base_url="http://x", now=late)
    assert sent and all(uid == "U0" for uid, _ in slack.sent)
    assert all(text.count("<http://x/accounts/") <= 3 for _, text in slack.sent)
    assert "meant for" in slack.sent[0][1]
    assert send_digests(synced, org, slack, base_url="http://x", now=late) == []  # not twice a day
