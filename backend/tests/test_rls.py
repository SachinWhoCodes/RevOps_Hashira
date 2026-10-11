"""C3: database-enforced separation between companies (Postgres row-level security).

Everything here runs as a non-owner role in the `casefile_app` group — exactly like production's
`casefile_runtime` — by switching role inside the test transaction (owners and superusers bypass RLS,
so the rest of the suite cannot prove it).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from casefile.api.deps import get_db
from casefile.api.main import app
from casefile.bootstrap import ensure_org
from casefile.db.models import Account
from casefile.db.tenant import set_tenant, system_scope

CSRF = {"x-casefile-csrf": "1"}
TENANT_TABLES = [
    "account",
    "account_case",
    "activity",
    "app_user",
    "approval",
    "assessment",
    "audit_log",
    "backtest_run",
    "change_item",
    "change_set",
    "config_version",
    "connection",
    "contact",
    "deal",
    "email_label",
    "eval_run",
    "execution_attempt",
    "external_ref",
    "field_history",
    "identity_override",
    "job",
    "llm_cache",
    "llm_call",
    "play_outcome",
    "policy",
    "raw_record",
    "renewal_outcome",
    "signal",
    "sync_run",
    "usage_daily",
    "user_invite",
    "org",
    "backtest_result",
]


PROBE_ROLE = "casefile_rls_probe"


@pytest.fixture(scope="session", autouse=True)
def probe_role(engine: Engine) -> None:
    """One committed, login-less member of casefile_app. Creating it inside each test transaction made
    concurrent test transactions wait on each other (adding a member locks the group role)."""
    with engine.connect() as conn:
        conn.execute(
            text(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'casefile_rls_probe') "
                "THEN CREATE ROLE casefile_rls_probe NOLOGIN IN ROLE casefile_app; END IF; END $$"
            )
        )
        conn.commit()


def as_runtime_role(session: Session) -> None:
    session.execute(text(f"SET ROLE {PROBE_ROLE}"))  # undone when the test's transaction rolls back


@pytest.fixture
def two_orgs(db: Session) -> Iterator[tuple[Session, str, str]]:
    a = ensure_org(db, slug="rls-a", name="A", timezone="UTC", internal_domains=["a.test"])
    b = ensure_org(db, slug="rls-b", name="B", timezone="UTC", internal_domains=["b.test"])
    db.add_all(
        [
            Account(org_id=a.id, crm_source="t", crm_id="1", name="Alpha", domains=["alpha.test"]),
            Account(org_id=b.id, crm_source="t", crm_id="1", name="Bravo", domains=["bravo.test"]),
        ]
    )
    db.flush()
    as_runtime_role(db)
    yield db, str(a.id), str(b.id)


def names(db: Session) -> set[str]:
    return set(db.scalars(text("select name from account")))


def test_no_organisation_set_means_every_table_looks_empty(two_orgs: tuple[Session, str, str]) -> None:
    db, _, _ = two_orgs
    db.execute(text("select set_config('app.org_id', '', true), set_config('app.bypass_rls', '', true)"))
    for table in TENANT_TABLES:
        assert db.scalar(text(f"select count(*) from {table}")) == 0, table


def test_each_organisation_sees_only_its_own_rows(two_orgs: tuple[Session, str, str]) -> None:
    db, a, b = two_orgs
    set_tenant(db, a)
    assert names(db) == {"Alpha"} and set(db.scalars(text("select name from org"))) == {"A"}
    set_tenant(db, b)
    assert names(db) == {"Bravo"}


def test_writing_into_another_organisation_is_refused(two_orgs: tuple[Session, str, str]) -> None:
    db, a, b = two_orgs
    set_tenant(db, a)
    nested = db.begin_nested()
    with pytest.raises(DBAPIError, match="row-level security"):
        db.execute(
            text(
                "insert into account (id, org_id, crm_source, crm_id, name, domains) "
                "values (gen_random_uuid(), :b, 't', '9', 'Sneaky', '{}')"
            ),
            {"b": b},
        )
    nested.rollback()
    # Moving a row to another organisation is refused too.
    nested = db.begin_nested()
    with pytest.raises(DBAPIError, match="row-level security"):
        db.execute(text("update account set org_id = :b where name = 'Alpha'"), {"b": b})
    nested.rollback()


def test_system_mode_sees_everything_and_does_not_leak(two_orgs: tuple[Session, str, str]) -> None:
    db, a, _ = two_orgs
    system_scope(db)
    assert names(db) == {"Alpha", "Bravo"}
    system_scope(db, False)
    set_tenant(db, a)
    assert names(db) == {"Alpha"}


# ---------------------------------------------------------------- the API, end to end, as the runtime role


@pytest.fixture(scope="module")
def runtime_client(synced: Session) -> Iterator[TestClient]:
    from casefile.ai.explain import explain_assessment, open_case_assessments
    from casefile.cases.service import recompute_all
    from casefile.db.models import Org

    org = synced.scalars(select(Org).where(Org.slug == "test-org")).one()
    recompute_all(synced, org)
    for aid in open_case_assessments(synced, org):
        explain_assessment(synced, aid, None)
    synced.commit()
    as_runtime_role(synced)
    app.dependency_overrides[get_db] = lambda: synced
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    synced.execute(text("RESET ROLE"))


def test_the_app_works_under_row_level_security(runtime_client: TestClient) -> None:
    c = runtime_client
    assert (
        c.post(
            "/api/auth/login",
            json={"email": "admin@lumora.test", "password": "test-password-1"},
            headers=CSRF,
        ).status_code
        == 200
    )
    queue = c.get("/api/queue?kind=risk").json()
    assert queue["items"], "the queue must not be empty under RLS"
    accounts = c.get("/api/accounts").json()
    assert len(accounts) > 10
    acme = next(a for a in accounts if a["name"] == "Acme Corp")
    page = c.get(f"/api/accounts/{acme['id']}").json()
    assert page["exists"] and page["assessment"]["levers"] and page["relationships"]["people"]
    for path in (
        "/api/inbox",
        "/api/team",
        "/api/plays",
        "/api/config",
        "/api/blind-spots",
        "/api/onboarding",
    ):
        assert c.get(path).status_code == 200, path
    assert c.get("/api/audit/verify").json()["ok"] is True


def test_self_service_signup_works_under_row_level_security(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Register → confirm → Setup and Connections pages, all as the runtime role (no owner bypass)."""
    from casefile.config import get_settings

    monkeypatch.setenv("OUTBOX_DIR", str(tmp_path))
    get_settings.cache_clear()
    as_runtime_role(db)
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as c:
            body = {"company": "Initech", "email": "peter@initech.test", "password": "a-long-enough-password"}
            assert c.post("/api/auth/register", json=body, headers=CSRF).status_code == 200
            text = next(tmp_path.glob("*.eml")).read_text()
            token = next(w for w in text.split() if "/verify/" in w).rsplit("/", 1)[1]
            me = c.post(f"/api/auth/verify/{token}", headers=CSRF)
            assert me.status_code == 200 and me.json()["org_mode"] == "setup"
            assert c.get("/api/onboarding").status_code == 200
            assert c.get("/api/connect").json()["hubspot"] == {"read": [], "write": []}
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
