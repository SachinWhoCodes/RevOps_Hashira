"""Deleting one organisation's data on request: everything goes, a receipt stays, others are untouched."""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile import onboarding as ob
from casefile import secretbox
from casefile.audit.log import verify_chain
from casefile.bootstrap import ensure_connection, ensure_org, upsert_user
from casefile.config import get_settings
from casefile.db.models import (
    Account,
    AuditLog,
    Base,
    ConnectionKind,
    Contact,
    OrgDeletion,
    Signup,
    UserRole,
    Zone,
)
from casefile.deletion import DeletionError, delete_org


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("SECRETS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("ORG_SLUG", "the-demo")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def company(db: Session, slug: str) -> tuple[object, object]:
    org = ensure_org(db, slug=slug, name=f"{slug} Ltd", timezone="UTC", internal_domains=[f"{slug}.test"])
    admin = upsert_user(
        db, org, email=f"admin@{slug}.test", role=UserRole.admin, password="a-long-password-1"
    )
    ob.record_permission(db, org, admin, ob.permission_statements(org))  # writes to the audit chain
    acc = Account(
        org_id=org.id, crm_source="t", crm_id="1", name="Customer Co", domains=[f"cust-{slug}.test"]
    )
    db.add(acc)
    db.flush()
    db.add(Contact(org_id=org.id, account_id=acc.id, email=f"dana@cust-{slug}.test", name="Dana"))
    return org, admin


def test_delete_org_removes_everything_and_leaves_a_receipt(db: Session, env: Path) -> None:
    client_org, _ = company(db, "client")
    other_org, _ = company(db, "other")
    gmail = ensure_connection(db, client_org, ConnectionKind.gmail, name="sales@client.test")  # type: ignore[arg-type]
    gmail.secret_enc = secretbox.seal(
        json.dumps({"refresh_token": "1//client-refresh", "client": "platform"})
    )
    ensure_connection(db, client_org, ConnectionKind.slack, name="workspace", zone=Zone.write)  # type: ignore[arg-type]
    db.add(
        Signup(
            email="admin@client.test",
            company="client Ltd",
            password_hash="x",
            token_hash="c" * 64,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            org_id=client_org.id,  # type: ignore[attr-defined]
        )
    )
    uploads = get_settings().upload_dir / str(client_org.id)  # type: ignore[attr-defined]
    uploads.mkdir(parents=True)
    (uploads / "usage.csv").write_text("account_key,date\n")
    db.flush()
    revoked: list[str] = []

    def google(request: httpx.Request) -> httpx.Response:
        revoked.append(dict(urllib.parse.parse_qsl(request.content.decode()))["token"])
        return httpx.Response(200)

    before_other = verify_chain(db, other_org.id)  # type: ignore[attr-defined]
    out = delete_org(
        db,
        "client",
        deleted_by="test",
        reason="asked after the demo",
        google=httpx.Client(transport=httpx.MockTransport(google)),
    )
    org_id = client_org.id  # type: ignore[attr-defined]
    for table in Base.metadata.sorted_tables:
        if "org_id" in table.c and table.name != "org_deletion":
            assert db.scalar(select(func.count()).select_from(table).where(table.c.org_id == org_id)) == 0, (
                table.name
            )
    assert out["audit_entries"] >= 1 and out["audit_chain_ok"] and out["audit_head_hash"]
    assert out["by_table"]["account"] == 1 and out["by_table"]["contact"] == 1
    assert revoked == ["1//client-refresh"] and out["google_grants_revoked"] == ["sales@client.test"]
    assert any("Slack" in step for step in out["client_still_to_do"])
    assert not uploads.exists()
    receipt = db.get(OrgDeletion, __import__("uuid").UUID(out["receipt"]))
    assert receipt is not None and receipt.slug == "client" and receipt.reason == "asked after the demo"
    assert "client.test" not in json.dumps(receipt.counts)  # counts only: no names, emails or content
    # The other organisation and its audit chain are untouched.
    assert verify_chain(db, other_org.id) == before_other  # type: ignore[attr-defined]
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.org_id == other_org.id))  # type: ignore[attr-defined]
    # The append-only guard is back on for everyone else.
    with pytest.raises(Exception, match="append-only"):
        nested = db.begin_nested()
        try:
            db.execute(AuditLog.__table__.delete().where(AuditLog.org_id == other_org.id))  # type: ignore[attr-defined]
        finally:
            nested.rollback()


def test_the_demo_organisation_needs_an_explicit_flag(db: Session, env: Path) -> None:
    company(db, "the-demo")
    with pytest.raises(DeletionError, match="demo organisation"):
        delete_org(db, "the-demo", deleted_by="test")
    with pytest.raises(DeletionError, match="not found"):
        delete_org(db, "no-such-org", deleted_by="test")


def test_truncating_the_audit_log_is_still_refused(db: Session) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="append-only"), db.begin_nested():
        db.execute(text("TRUNCATE audit_log"))
