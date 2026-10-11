from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from casefile.config import get_settings

BACKEND = Path(__file__).resolve().parents[1]
TEST_TODAY = date(2026, 10, 10)


def _test_url() -> str:
    return os.environ.get("TEST_DATABASE_URL") or get_settings().test_database_url


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = _test_url()
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()  # type: ignore[assignment]
    command.upgrade(cfg, "head")
    eng = create_engine(url, future=True)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    """A session whose work is rolled back after the test, even if the code under test commits."""
    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture(scope="session")
def small_synth(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A small, fixed synthetic company shared by tests (generated once per test session)."""
    from casefile.synth.generator import SynthParams, generate

    out = tmp_path_factory.mktemp("synth")
    generate(SynthParams(seed=11, n_accounts=30, history_days=400, today=TEST_TODAY), out)
    return out


@pytest.fixture(scope="module")
def synced(engine: Engine, small_synth: Path) -> Iterator[Session]:
    """The small synthetic company loaded through the real fixture sync, shared by one test module
    and rolled back afterwards."""
    from casefile.bootstrap import ensure_connection, ensure_org, upsert_user
    from casefile.db.models import ConnectionKind, UserRole
    from casefile.ingest.sync_runner import run_sync

    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
    org = ensure_org(
        session,
        slug="test-org",
        name="Lumora",
        timezone="Asia/Kolkata",
        internal_domains=["lumora.test"],
        mode="live",  # the shared test company behaves like the live demo organisation
    )
    upsert_user(
        session, org, email="admin@lumora.test", name="Admin", role=UserRole.admin, password="test-password-1"
    )
    conn = ensure_connection(
        session, org, ConnectionKind.fixture, name="synthetic", config={"dir": str(small_synth)}
    )
    session.commit()
    run = run_sync(session, conn.id)
    assert run.status.value == "succeeded", run.error
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()
