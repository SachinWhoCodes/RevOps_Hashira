"""C4: onboarding — credentials at rest, connecting HubSpot safely, usage upload, modes, the API."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from casefile import onboarding as ob
from casefile.api.deps import get_db
from casefile.api.main import app
from casefile.bootstrap import ensure_connection, ensure_org, upsert_user
from casefile.config import get_settings
from casefile.connectors.base import AuthError
from casefile.connectors.registry import connection_secret
from casefile.db import tenant
from casefile.db.models import (
    Account,
    AppUser,
    AuditLog,
    BacktestRun,
    Connection,
    ConnectionKind,
    EvidenceLabel,
    Job,
    Org,
    UserRole,
    Zone,
)
from casefile.secretbox import SecretsUnavailable, seal, unseal
from casefile.worker.__main__ import held_in_setup

CSRF = {"x-casefile-csrf": "1"}
# Deliberately not shaped like a real HubSpot key, so secret scanners never mistake it for one.
TOKEN = "fake-hubspot-test-token-not-a-real-key"


@pytest.fixture
def secrets_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("SECRETS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class FakeLive:
    def __init__(
        self, portal: str = "999", refused: set[str] | None = None, bad: bool = False, writes: bool = False
    ) -> None:
        self.portal, self.refused, self.bad, self.writes = portal, refused or set(), bad, writes
        self.asked: list[str] = []

    def account(self) -> dict[str, object]:
        if self.bad:
            raise AuthError("401", status=401)
        return {
            "portal_id": self.portal,
            "time_zone": "UTC",
            "currency": "USD",
            "ui_domain": "app.hubspot.com",
        }

    def can_read(self, objects: list[str]) -> dict[str, str]:
        self.asked = list(objects)
        return {o: ("refused" if o in self.refused else "ok") for o in objects}

    def can_write(self) -> dict[str, str]:
        return {k: ("ok" if self.writes else "refused") for k in ("tasks", "notes", "deals")}


def new_company(db: Session, slug: str = "pilot") -> tuple[Org, AppUser]:
    org = ensure_org(db, slug=slug, name="Pilot Co", timezone="UTC", internal_domains=["pilot.test"])
    admin = upsert_user(db, org, email=f"admin@{slug}.test", role=UserRole.admin, password="pilot-password-1")
    return org, admin


def permitted(db: Session, slug: str = "pilot") -> tuple[Org, AppUser]:
    org, admin = new_company(db, slug)
    ob.record_permission(db, org, admin, ob.permission_statements(org))
    return org, admin


# ---------------------------------------------------------------- credentials at rest


def test_tokens_are_encrypted_and_there_is_no_implicit_default(
    db: Session, secrets_key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    sealed = seal(TOKEN)
    assert TOKEN not in sealed and unseal(sealed) == TOKEN
    org, _ = new_company(db)
    conn = ensure_connection(db, org, ConnectionKind.hubspot, name="crm")
    monkeypatch.setenv("READ_HUBSPOT_TOKEN", "server-wide-token")
    get_settings.cache_clear()
    with pytest.raises(AuthError, match="no credential"):
        connection_secret(conn)  # never falls back to the server-wide token (another company's CRM)
    conn.secret_ref = "READ_HUBSPOT_TOKEN"  # named explicitly by the operator: allowed
    assert connection_secret(conn) == "server-wide-token"
    conn.secret_enc = sealed
    assert connection_secret(conn) == TOKEN  # the pasted one wins
    monkeypatch.setenv("SECRETS_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    with pytest.raises(SecretsUnavailable):
        unseal(sealed)  # a different key cannot read it


# ---------------------------------------------------------------- connecting HubSpot


def test_connecting_hubspot_stores_only_ciphertext_and_queues_the_first_sync(
    db: Session, secrets_key: None
) -> None:
    org, admin = new_company(db)
    with pytest.raises(ob.OnboardingError, match="permission"):
        ob.connect_hubspot(db, org, admin, TOKEN, live=FakeLive())  # type: ignore[arg-type]
    ob.record_permission(db, org, admin, ob.permission_statements(org))
    out = ob.connect_hubspot(db, org, admin, TOKEN, live=FakeLive(refused={"emails"}))  # type: ignore[arg-type]
    assert out["portal_id"] == "999" and out["warnings"] and "emails" in out["warnings"][0]
    conn = db.get(Connection, uuid.UUID(out["connection_id"]))
    assert conn is not None and conn.secret_ref is None and conn.secret_enc and TOKEN not in conn.secret_enc
    assert TOKEN not in json.dumps(conn.config)
    audits = db.scalars(select(AuditLog).where(AuditLog.org_id == org.id)).all()
    assert not any(TOKEN in json.dumps(a.details) for a in audits)
    assert "connection_added" in {a.action for a in audits}
    assert db.scalar(select(Job).where(Job.kind == "sync_connection", Job.dedupe_key == f"sync:{conn.id}"))
    assert connection_secret(conn) == TOKEN


def test_hubspot_tokens_that_cannot_read_enough_or_point_at_the_wrong_portal_are_refused(
    db: Session, secrets_key: None
) -> None:
    org, admin = permitted(db)
    with pytest.raises(ob.OnboardingError, match="did not accept"):
        ob.connect_hubspot(db, org, admin, TOKEN, live=FakeLive(bad=True))  # type: ignore[arg-type]
    with pytest.raises(ob.OnboardingError, match="cannot read: deals"):
        ob.connect_hubspot(db, org, admin, TOKEN, live=FakeLive(refused={"deals"}))  # type: ignore[arg-type]
    # Another company already reads this portal: the same CRM must never feed two organisations.
    other, _ = new_company(db, "other")
    ensure_connection(db, other, ConnectionKind.hubspot, name="crm", config={"portal_id": "777"})
    with pytest.raises(ob.OnboardingError, match="another Casefile organisation"):
        ob.connect_hubspot(db, org, admin, TOKEN, live=FakeLive(portal="777"))  # type: ignore[arg-type]
    # The portal Casefile writes sandbox changes into is never read as a CRM.
    ensure_connection(
        db, org, ConnectionKind.hubspot, name="sandbox", zone=Zone.write, config={"portal_id": "555"}
    )
    with pytest.raises(ob.OnboardingError, match="sandbox"):
        ob.connect_hubspot(db, org, admin, TOKEN, live=FakeLive(portal="555"))  # type: ignore[arg-type]
    assert not db.scalar(select(Connection).where(Connection.org_id == org.id, Connection.name == "crm"))
    ob.connect_hubspot(db, org, admin, TOKEN, live=FakeLive(portal="999"))  # type: ignore[arg-type]
    with pytest.raises(ob.OnboardingError, match="different portal"):
        ob.connect_hubspot(db, org, admin, TOKEN, live=FakeLive(portal="1000"))  # type: ignore[arg-type]


# ---------------------------------------------------------------- usage upload


def test_usage_upload_is_validated_matched_and_stored(db: Session, secrets_key: None) -> None:
    org, admin = permitted(db)
    db.add(Account(org_id=org.id, crm_source="t", crm_id="c1", name="Acme", domains=["acme.test"]))
    db.flush()
    with pytest.raises(ob.OnboardingError, match="missing columns: key_actions"):
        ob.upload_usage(db, org, admin, "u.csv", b"account_key,date,active_users\nacme.test,2026-09-01,3\n")
    with pytest.raises(ob.OnboardingError, match="line 3"):
        ob.upload_usage(
            db,
            org,
            admin,
            "u.csv",
            b"account_key,date,active_users,key_actions\na,2026-09-01,1,2\nb,soon,1,2\n",
        )
    body = (
        b"account_key,date,active_users,key_actions\nACME.test,2026-09-01,3,10\nunknown.test,2026-09-02,1,1\n"
    )
    out = ob.upload_usage(db, org, admin, "../../etc/usage.csv", body)
    assert out["rows"] == 2 and out["accounts_in_file"] == 2 and out["matched_now"] == 1
    conn = db.scalars(
        select(Connection).where(Connection.org_id == org.id, Connection.kind == ConnectionKind.usage_csv)
    ).one()
    path = Path(conn.config["path"])
    assert (
        path.read_bytes() == body
        and path.parent.name == str(org.id)
        and conn.config["filename"] == "usage.csv"
    )
    assert conn.cursor == {}


# ---------------------------------------------------------------- modes


def test_new_organisations_start_in_setup_and_setup_holds_ai_work(db: Session) -> None:
    org, _ = new_company(db)
    assert ob.org_mode(org) == "setup"
    assert ob.org_mode(Org(slug="x", name="x", settings={})) == "setup"  # no mode: fail safe
    job = Job(org_id=org.id, kind="write_explanation", payload={})
    assert held_in_setup(db, job)
    assert not held_in_setup(db, Job(org_id=org.id, kind="sync_connection", payload={}))
    org.settings = {**org.settings, "mode": "shadow"}
    assert not held_in_setup(db, job)


def test_from_setup_to_shadow_to_live_and_back(db: Session) -> None:
    org, admin = new_company(db)
    with pytest.raises(ob.OnboardingError, match="data permission"):
        ob.start_shadow(db, org, admin)
    ob.record_permission(db, org, admin, ob.permission_statements(org))
    crm = ensure_connection(db, org, ConnectionKind.fixture, name="synthetic", config={"dir": "x"})
    crm.last_success_at = datetime.now(UTC)
    with pytest.raises(ob.OnboardingError, match="back-test"):
        ob.start_shadow(db, org, admin)
    db.add(
        BacktestRun(
            org_id=org.id,
            status="done",
            label=EvidenceLabel.simulated,
            engine_version="t",
            config={},
            metrics={},
        )
    )
    db.add(Account(org_id=org.id, crm_source="t", crm_id="a", name="Acme", domains=["acme.test"]))
    db.flush()
    ob.start_shadow(db, org, admin)
    assert ob.org_mode(org) == "shadow"
    kinds = {j.kind for j in db.scalars(select(Job).where(Job.org_id == org.id))}
    assert {"read_emails", "recompute_account"} <= kinds  # the work setup mode held back
    with pytest.raises(ob.OnboardingError, match="executor is running"):
        ob.go_live(db, org, admin, "a long enough reason to override")
    writer = ensure_connection(db, org, ConnectionKind.hubspot, name="w", zone=Zone.write)
    writer.cursor = {"heartbeat": datetime.now(UTC).isoformat(), "writer": True}
    db.flush()
    with pytest.raises(ob.OnboardingError, match="give a reason"):
        ob.go_live(db, org, admin, None)  # 0 days of shadow, 0 decisions
    ob.go_live(db, org, admin, "pilot sponsor reviewed the shadow results")
    assert ob.org_mode(org) == "live"
    ob.back_to_shadow(db, org, admin, "pausing writes")
    assert ob.org_mode(org) == "shadow"
    ob.back_to_setup(db, org, admin, None)
    with pytest.raises(ob.OnboardingError, match="cannot switch from setup to live"):
        ob._change_mode(db, org, admin, "live", None)
    changes = [
        (a.details["from"], a.details["to"])
        for a in db.scalars(
            select(AuditLog)
            .where(AuditLog.org_id == org.id, AuditLog.action == "org_mode_changed")
            .order_by(AuditLog.seq)
        )
    ]
    assert changes == [("setup", "shadow"), ("shadow", "live"), ("live", "shadow"), ("shadow", "setup")]
    live_audit = db.scalars(
        select(AuditLog)
        .where(AuditLog.org_id == org.id, AuditLog.action == "org_mode_changed")
        .order_by(AuditLog.seq)
    ).all()[1]
    assert set(live_audit.details["overridden"]) == {"shadow_days", "decisions"}


def test_writer_heartbeat_must_be_fresh(db: Session) -> None:
    org, _ = new_company(db)
    org.settings = {**org.settings, "mode": "shadow"}
    writer = ensure_connection(db, org, ConnectionKind.hubspot, name="w", zone=Zone.write)
    writer.cursor = {"heartbeat": (datetime.now(UTC) - timedelta(hours=1)).isoformat(), "writer": True}
    checks = {c["key"]: c["ok"] for c in ob.live_checks(db, org)}
    assert checks["in_shadow"] and not checks["writer_ready"]


def test_company_details_are_validated(db: Session) -> None:
    org, admin = new_company(db)
    with pytest.raises(ob.OnboardingError, match="time zone"):
        ob.update_company(db, org, admin, name="P", timezone="Mars/Olympus", internal_domains=["p.test"])
    with pytest.raises(ob.OnboardingError, match="email domain"):
        ob.update_company(db, org, admin, name="P", timezone="UTC", internal_domains=["not a domain"])
    assert ob.update_company(
        db, org, admin, name="Pilot", timezone="Asia/Kolkata", internal_domains=["@Pilot.test", "pilot.io"]
    )
    assert org.internal_domains == ["pilot.io", "pilot.test"] and org.timezone == "Asia/Kolkata"
    assert not ob.update_company(
        db, org, admin, name="Pilot", timezone="Asia/Kolkata", internal_domains=["pilot.io", "pilot.test"]
    )


# ---------------------------------------------------------------- the operator's create-org command


def test_create_org_command(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from casefile import cli
    from casefile.db import session as session_module

    @contextmanager
    def test_scope() -> Iterator[Session]:
        yield db
        db.flush()

    monkeypatch.setattr(session_module, "session_scope", test_scope)
    monkeypatch.setattr(tenant, "_process_system", False)  # the CLI switches the process to system mode
    runner = CliRunner()
    args = ["create-org", "--slug", "acme-pilot", "--name", "Acme", "--admin-email", "Ops@Acme.test"]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert "/invite/" in result.output and "setup mode" in result.output
    org = db.scalars(select(Org).where(Org.slug == "acme-pilot")).one()
    assert ob.org_mode(org) == "setup" and org.internal_domains == ["acme.test"]
    admin = db.scalars(select(AppUser).where(AppUser.org_id == org.id)).one()
    assert admin.email == "ops@acme.test" and admin.role == UserRole.admin and admin.password_hash is None
    again = runner.invoke(cli.app, [*args[:2], "acme-two", *args[3:]])
    assert again.exit_code != 0 and "another organisation" in again.output


# ---------------------------------------------------------------- API


@pytest.fixture(scope="module")
def pilot_client(synced: Session) -> Iterator[tuple[TestClient, Session]]:
    _org, _ = new_company(synced, "api-pilot")
    synced.commit()
    app.dependency_overrides[get_db] = lambda: synced
    with TestClient(app) as c:
        r = c.post(
            "/api/auth/login",
            json={"email": "admin@api-pilot.test", "password": "pilot-password-1"},
            headers=CSRF,
        )
        assert r.status_code == 200 and r.json()["org_mode"] == "setup"
        yield c, synced
    app.dependency_overrides.clear()


def test_onboarding_api(
    pilot_client: tuple[TestClient, Session], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    c, db = pilot_client
    monkeypatch.setenv("SECRETS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        status: dict[str, Any] = c.get("/api/onboarding").json()
        assert status["mode"] == "setup" and status["label"] == "observed"
        states = {s["key"]: s["state"] for s in status["steps"]}
        assert states == {
            "company": "done",
            "permission": "todo",
            "crm": "todo",
            "usage": "optional",
            "data": "todo",
            "backtest": "todo",
            "shadow": "todo",
            "live": "todo",
        }
        assert status["ai_estimate"]["label"] == "estimated" and len(status["start_shadow_blockers"]) == 3
        statements = next(s for s in status["steps"] if s["key"] == "permission")["data"]["statements"]
        assert (
            c.post(
                "/api/onboarding/permission", json={"statements": statements[:2]}, headers=CSRF
            ).status_code
            == 422
        )
        assert (
            c.post("/api/onboarding/permission", json={"statements": statements}, headers=CSRF).status_code
            == 200
        )
        monkeypatch.setattr(ob, "HubSpotLive", lambda token: FakeLive(portal="4242"))
        r = c.post("/api/onboarding/hubspot", json={"token": TOKEN}, headers=CSRF)
        assert r.status_code == 200 and r.json()["portal_id"] == "4242"
        bad = c.post("/api/onboarding/hubspot", json={"token": "has spaces in it"}, headers=CSRF)
        assert bad.status_code == 422
        csv = b"account_key,date,active_users,key_actions\nacme.test,2026-09-01,3,10\n"
        up = c.post("/api/onboarding/usage", files={"file": ("usage.csv", csv, "text/csv")}, headers=CSRF)
        assert up.status_code == 200 and up.json()["rows"] == 1
        assert "account_key" in c.get("/api/onboarding/usage/template").text
        blocked = c.post("/api/onboarding/mode", json={"to": "shadow"}, headers=CSRF)
        assert blocked.status_code == 422 and "first sync" in blocked.json()["detail"]
        assert c.post("/api/onboarding/mode", json={"to": "live"}, headers=CSRF).status_code == 422
        crm = next(s for s in c.get("/api/onboarding").json()["steps"] if s["key"] == "crm")
        assert crm["data"]["connections"][0]["config"]["portal_id"] == "4242"
        assert "path" not in json.dumps(crm)  # server paths and secrets never leave the API
        # One email, one organisation: an admin cannot add someone who logs in elsewhere.
        taken = c.post(
            "/api/team", json={"email": "admin@lumora.test", "name": "x", "role": "owner"}, headers=CSRF
        )
        assert taken.status_code == 409 and "another organisation" in taken.json()["detail"]
        # A CRM-synced owner whose email logs in elsewhere cannot be invited either.
        org = db.scalars(select(Org).where(Org.slug == "api-pilot")).one()
        twin = upsert_user(db, org, email="admin@lumora.test", role=UserRole.owner)
        db.commit()
        refused = c.post(f"/api/team/{twin.id}/invite", json={}, headers=CSRF)
        assert refused.status_code == 409 and "another Casefile organisation" in refused.json()["detail"]
    finally:
        get_settings.cache_clear()


def test_only_admins_see_onboarding(pilot_client: tuple[TestClient, Session]) -> None:
    _c, db = pilot_client
    org = db.scalars(select(Org).where(Org.slug == "api-pilot")).one()
    upsert_user(db, org, email="owner@api-pilot.test", role=UserRole.owner, password="owner-password-1")
    db.commit()
    with TestClient(app) as other:
        other.post(
            "/api/auth/login",
            json={"email": "owner@api-pilot.test", "password": "owner-password-1"},
            headers=CSRF,
        )
        assert other.get("/api/onboarding").status_code == 403
        assert other.post("/api/onboarding/mode", json={"to": "shadow"}, headers=CSRF).status_code == 403
        assert other.get("/api/auth/me").json()["org_mode"] == "setup"


def test_back_test_of_a_company_without_history_is_an_empty_report(db: Session) -> None:
    """Just connected, no renewals yet: the onboarding page shows an explanation, not an error."""
    from casefile.backtest.runner import BacktestConfig, run_backtest

    org, _ = new_company(db, "no-history")
    run = run_backtest(db, org, BacktestConfig())
    assert run.status == "done" and run.metrics["events"]["total"] == 0 and run.metrics["fallback"]
    step = next(s for s in ob.status(db, org)["steps"] if s["key"] == "backtest")
    assert step["state"] == "warn" and step["data"]["note"]
