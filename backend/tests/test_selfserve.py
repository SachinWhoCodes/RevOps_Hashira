"""Self-service: sign-up with email confirmation, password reset, connections pasted in the app, sealed
write credentials, the multi-company executor, AI caps. No real API is called (fake mailers, mock
transports, fake HubSpot)."""

from __future__ import annotations

import json
import urllib.parse
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile import connect as cx
from casefile import onboarding as ob
from casefile import secretbox
from casefile.api.deps import get_db
from casefile.api.main import app
from casefile.bootstrap import ensure_connection, ensure_org, upsert_user
from casefile.config import Settings, get_settings
from casefile.db import tenant
from casefile.db.models import (
    AppUser,
    AuditLog,
    Connection,
    ConnectionKind,
    LlmCall,
    Org,
    Signup,
    UserRole,
    Zone,
)
from casefile.executor.settings import ExecutorSettings
from casefile.executor.tenants import Tenants, build_writer_set
from casefile.executor.unseal import UnsealError, unseal
from casefile.invites import token_hash
from casefile.mailer import BrevoMailer, Mail, MailError, OutboxMailer, get_mailer
from casefile.sealing import new_keypair, seal_for_executor
from casefile.signup import SignupError, complete_signup, forgot_password, pending, start_signup

CSRF = {"x-casefile-csrf": "1"}
PASSWORD = "a-long-enough-password"


class FakeMailer:
    def __init__(self) -> None:
        self.sent: list[Mail] = []

    def send(self, mail: Mail) -> str:
        self.sent.append(mail)
        return f"fake-{len(self.sent)}"

    def link(self, path: str) -> str:
        for mail in reversed(self.sent):
            for word in mail.text.split():
                if f"/{path}/" in word:
                    return word.rsplit("/", 1)[1]
        raise AssertionError(f"no /{path}/ link in {[m.subject for m in self.sent]}")


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[ExecutorSettings]:
    private, public = new_keypair()
    monkeypatch.setenv("SECRETS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("EXECUTOR_SEAL_PUBLIC_KEY", public)
    monkeypatch.setenv("OUTBOX_DIR", str(tmp_path / "outbox"))
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    get_settings.cache_clear()
    yield ExecutorSettings(executor_seal_private_key=private)  # type: ignore[arg-type]
    get_settings.cache_clear()


def company(db: Session, slug: str = "acme", *, permitted: bool = True) -> tuple[Org, AppUser]:
    org = ensure_org(db, slug=slug, name=slug.title(), timezone="UTC", internal_domains=[f"{slug}.test"])
    admin = upsert_user(db, org, email=f"admin@{slug}.test", role=UserRole.admin, password=PASSWORD)
    if permitted:
        ob.record_permission(db, org, admin, ob.permission_statements(org))
    return org, admin


# ---------------------------------------------------------------- sealing (rule R1)


def test_sealed_credentials_open_only_with_the_executor_key(keys: ExecutorSettings) -> None:
    sealed = seal_for_executor("xoxb-secret")
    assert "xoxb-secret" not in sealed
    private = keys.executor_seal_private_key
    assert private is not None and unseal(sealed, private.get_secret_value()) == "xoxb-secret"
    other_private, _ = new_keypair()
    with pytest.raises(UnsealError):
        unseal(sealed, other_private)
    tampered = sealed[:-4] + ("AAAA" if not sealed.endswith("AAAA") else "BBBB")
    with pytest.raises(UnsealError):
        unseal(tampered, private.get_secret_value())
    # The app's settings never hold the private half.
    assert not [f for f in Settings.model_fields if "private" in f]


# ---------------------------------------------------------------- email


def test_brevo_request_shape_and_errors() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if json.loads(request.content)["to"][0]["email"] == "bad@x.test":
            return httpx.Response(400, json={"code": "invalid_parameter", "message": "sender not valid"})
        return httpx.Response(201, json={"messageId": "<abc@brevo>"})

    client = httpx.Client(transport=httpx.MockTransport(handler), headers={"api-key": "k"})
    mailer = BrevoMailer("k", "noreply@casefile.test", "Casefile", client=client)
    assert mailer.send(Mail("ok@x.test", "Hi", "Open https://x.test/verify/abc")) == "<abc@brevo>"
    body = json.loads(seen[0].content)
    assert seen[0].url == "https://api.brevo.com/v3/smtp/email" and seen[0].headers["api-key"] == "k"
    assert body["sender"] == {"email": "noreply@casefile.test", "name": "Casefile"}
    assert '<a href="https://x.test/verify/abc">' in body["htmlContent"] and body["textContent"].startswith(
        "Open"
    )
    with pytest.raises(MailError, match="sender not valid"):
        mailer.send(Mail("bad@x.test", "Hi", "x"))


def test_without_brevo_only_a_local_server_writes_to_the_outbox(
    keys: ExecutorSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailer = get_mailer()
    assert isinstance(mailer, OutboxMailer)
    assert mailer.send(Mail("a@b.test", "Hello", "body")).startswith("outbox:")
    assert len(list(get_settings().outbox_dir.glob("*.eml"))) == 1
    monkeypatch.setenv("APP_BASE_URL", "https://casefile.example.com")
    get_settings.cache_clear()
    with pytest.raises(MailError, match="not configured"):
        get_mailer()  # a public server never pretends to have sent an email


# ---------------------------------------------------------------- sign-up


def register(db: Session, mailer: FakeMailer, email: str = "founder@newco.test", **kw: Any) -> None:
    start_signup(
        db,
        mailer,
        company=kw.get("company", "NewCo"),
        name="Fran",
        email=email,
        password=kw.get("password", PASSWORD),
        timezone="Asia/Kolkata",
        ip=kw.get("ip", "10.0.0.1"),
    )


def test_signup_creates_nothing_until_the_email_is_confirmed(db: Session, keys: ExecutorSettings) -> None:
    mailer = FakeMailer()
    register(db, mailer)
    assert db.scalar(select(Org).where(Org.name == "NewCo")) is None  # nothing exists yet
    first = mailer.link("verify")
    register(db, mailer)  # registering again supersedes the first link
    second = mailer.link("verify")
    with pytest.raises(SignupError, match="not valid"):
        pending(db, first)
    user = complete_signup(db, second)
    org = db.get(Org, user.org_id)
    assert org is not None and ob.org_mode(org) == "setup" and org.internal_domains == ["newco.test"]
    assert user.role == UserRole.admin and user.is_active and org.timezone == "Asia/Kolkata"
    with pytest.raises(SignupError, match="already used"):
        complete_signup(db, second)
    assert db.scalars(
        select(AuditLog).where(AuditLog.org_id == org.id, AuditLog.action == "org_registered")
    ).one()


def test_signup_rules(db: Session, keys: ExecutorSettings) -> None:
    mailer = FakeMailer()
    with pytest.raises(SignupError, match="at least 12"):
        register(db, mailer, password="short")
    with pytest.raises(SignupError, match="valid work email"):
        register(db, mailer, email="not-an-email")
    register(db, mailer, email="me@gmail.com", company="Solo")
    user = complete_signup(db, mailer.link("verify"))
    org = db.get(Org, user.org_id)
    assert org is not None and org.internal_domains == []  # a personal mailbox is not "our staff"
    # An existing account gets a "you already have an account" email, never a second organisation.
    register(db, mailer, email="me@gmail.com", company="Again")
    assert (
        mailer.sent[-1].subject == "You already have a Casefile account"
        and "/verify/" not in mailer.sent[-1].text
    )
    # Throttles: per email and per network.
    for _ in range(3):
        register(db, mailer, email="spam@x.test")
    with pytest.raises(SignupError, match="too many attempts"):
        register(db, mailer, email="spam@x.test")
    for i in range(20):
        db.add(
            Signup(
                email=f"bot{i}@x.test",
                company="x",
                password_hash="x",
                token_hash=uuid.uuid4().hex * 2,
                ip_hash=db.scalars(select(Signup.ip_hash).where(Signup.email == "spam@x.test")).first(),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
    db.flush()
    with pytest.raises(SignupError, match="this network"):
        register(db, mailer, email="other@x.test")
    db.add(
        Signup(
            email="late@x.test",
            company="Late",
            password_hash="x",
            token_hash=token_hash("expired-token"),
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    db.flush()
    with pytest.raises(SignupError, match="expired"):
        pending(db, "expired-token")
    with pytest.raises(SignupError, match="not valid"):
        complete_signup(db, "no-such-token")


def test_forgot_password_mails_a_reset_link_only_for_real_accounts(
    db: Session, keys: ExecutorSettings
) -> None:
    _, admin = company(db, "resetco")
    mailer = FakeMailer()
    forgot_password(db, mailer, "nobody@resetco.test")
    assert mailer.sent == []  # no account: nothing sent (and the API answers the same either way)
    forgot_password(db, mailer, "ADMIN@resetco.test")
    assert mailer.sent[-1].subject == "Reset your Casefile password" and "/invite/" in mailer.sent[-1].text
    for _ in range(5):
        forgot_password(db, mailer, admin.email)
    assert len(mailer.sent) == 3  # at most 3 per hour


# ---------------------------------------------------------------- HubSpot write access


class FakeHub:
    def __init__(self, portal: str = "77", refused: set[str] | None = None) -> None:
        self.portal, self.refused = portal, refused or set()

    def account(self) -> dict[str, object]:
        return {"portal_id": self.portal}

    def can_write(self) -> dict[str, str]:
        return {k: ("refused" if k in self.refused else "ok") for k in ("tasks", "notes", "deals")}


def test_hubspot_write_token_is_checked_and_sealed(db: Session, keys: ExecutorSettings) -> None:
    org, admin = company(db)
    with pytest.raises(cx.ConnectError, match="read-only HubSpot app first"):
        cx.connect_hubspot_write(db, org, admin, "pat-write-token-123", live=FakeHub())  # type: ignore[arg-type]
    ensure_connection(db, org, ConnectionKind.hubspot, name="crm", config={"portal_id": "77"})
    with pytest.raises(cx.ConnectError, match="portal 88"):
        cx.connect_hubspot_write(db, org, admin, "pat-write-token-123", live=FakeHub("88"))  # type: ignore[arg-type]
    with pytest.raises(cx.ConnectError, match="cannot update deals"):
        cx.connect_hubspot_write(db, org, admin, "pat-write-token-123", live=FakeHub(refused={"deals"}))  # type: ignore[arg-type]
    cx.connect_hubspot_write(db, org, admin, "pat-write-token-123", live=FakeHub())  # type: ignore[arg-type]
    conn = db.scalars(
        select(Connection).where(Connection.org_id == org.id, Connection.zone == Zone.write)
    ).one()
    assert conn.secret_sealed and conn.secret_enc is None and "pat-write" not in conn.secret_sealed
    with pytest.raises(Exception):  # noqa: B017 - the app's own key cannot open it
        secretbox.unseal(conn.secret_sealed)
    private = keys.executor_seal_private_key
    assert (
        private is not None
        and unseal(conn.secret_sealed, private.get_secret_value()) == "pat-write-token-123"
    )


def test_a_read_token_that_can_write_is_refused(db: Session, keys: ExecutorSettings) -> None:
    from tests.test_onboarding import FakeLive

    org, admin = company(db)
    with pytest.raises(ob.OnboardingError, match="can also change records"):
        ob.connect_hubspot(db, org, admin, "fake-read-token-for-tests", live=FakeLive(writes=True))  # type: ignore[arg-type]


# ---------------------------------------------------------------- Slack


def slack_transport(*, scopes: str, team: str = "T1", app_ok: bool = True) -> httpx.Client:
    people = {"admin@acme.test": "U_ADMIN"}

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[1]
        form = dict(urllib.parse.parse_qsl(request.content.decode()))
        if method == "auth.test":
            return httpx.Response(
                200,
                json={"ok": True, "team_id": team, "team": "Acme", "user_id": "B1"},
                headers={"x-oauth-scopes": scopes},
            )
        if method == "apps.connections.open":
            return httpx.Response(
                200,
                json={"ok": app_ok, "url": "wss://x"} if app_ok else {"ok": False, "error": "invalid_auth"},
            )
        if method == "users.lookupByEmail":
            uid = people.get(form.get("email", ""))
            return httpx.Response(
                200,
                json={"ok": True, "user": {"id": uid}} if uid else {"ok": False, "error": "users_not_found"},
            )
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_slack_connect_checks_scopes_links_people_and_splits_tokens(
    db: Session, keys: ExecutorSettings
) -> None:
    org, admin = company(db)
    upsert_user(db, org, email="owner@acme.test", role=UserRole.owner)
    full = "chat:write,im:write,users:read,users:read.email"
    with pytest.raises(cx.ConnectError, match=r"missing scopes: users:read\.email"):
        cx.connect_slack(
            db,
            org,
            admin,
            "xoxb-1234567890",
            "xapp-1234567890",
            client=slack_transport(scopes="chat:write,im:write,users:read"),
        )
    with pytest.raises(cx.ConnectError, match="starts with xapp-"):
        cx.connect_slack(
            db, org, admin, "xoxb-1234567890", "xoxb-1234567890", client=slack_transport(scopes=full)
        )
    with pytest.raises(cx.ConnectError, match="app-level token"):
        cx.connect_slack(
            db,
            org,
            admin,
            "xoxb-1234567890",
            "xapp-1234567890",
            client=slack_transport(scopes=full, app_ok=False),
        )
    out = cx.connect_slack(
        db, org, admin, "xoxb-1234567890", "xapp-1234567890", client=slack_transport(scopes=full)
    )
    assert out["linked"] == 1 and out["unmatched"] == ["owner@acme.test"] and out["fallback"]
    conn = db.scalars(
        select(Connection).where(Connection.org_id == org.id, Connection.kind == ConnectionKind.slack)
    ).one()
    assert conn.config["team_id"] == "T1" and conn.config["fallback_user_id"] == "U_ADMIN"
    assert secretbox.unseal(conn.secret_enc or "") == "xapp-1234567890"  # the slackbot can open this one
    assert "xoxb" not in (conn.secret_sealed or "")  # the bot token is for the executor only
    other, other_admin = company(db, "other")
    with pytest.raises(cx.ConnectError, match="another Casefile organisation"):
        cx.connect_slack(
            db, other, other_admin, "xoxb-1234567890", "xapp-1234567890", client=slack_transport(scopes=full)
        )


# ---------------------------------------------------------------- Google


def google_transport(
    *, scope: str, mailbox: str = "sales@acme.test", refresh: bool = True
) -> tuple[httpx.Client, list[dict[str, str]]]:
    exchanges: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            exchanges.append(dict(urllib.parse.parse_qsl(request.content.decode())))
            body: dict[str, Any] = {"access_token": "ya29.x", "scope": scope, "expires_in": 3600}
            if refresh:
                body["refresh_token"] = "1//refresh"
            return httpx.Response(200, json=body)
        return httpx.Response(200, json={"emailAddress": mailbox})

    return httpx.Client(transport=httpx.MockTransport(handler)), exchanges


READ = "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/calendar.readonly"


def test_google_connect_read_and_drafts(
    db: Session, keys: ExecutorSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "platform.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "platform-secret")
    get_settings.cache_clear()
    org, admin = company(db)
    url = cx.google_start(org, admin, "read")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    assert q["client_id"] == "platform.apps.googleusercontent.com" and q["code_challenge_method"] == "S256"
    assert q["redirect_uri"].endswith("/api/oauth/google/callback") and "gmail.readonly" in q["scope"]
    assert q["include_granted_scopes"] == "false" and q["access_type"] == "offline"
    client, exchanges = google_transport(scope=READ)
    with pytest.raises(cx.ConnectError, match="sign in to Casefile as the person"):
        cx.google_finish(db, str(uuid.uuid4()), "code", q["state"], client=client)  # someone else's browser
    unticked, _ = google_transport(scope="https://www.googleapis.com/auth/gmail.readonly")
    with pytest.raises(cx.ConnectError, match=r"calendar\.readonly"):
        cx.google_finish(db, str(admin.id), "code", q["state"], client=unticked)
    out = cx.google_finish(db, str(admin.id), "code", q["state"], client=client)
    assert out == {"purpose": "read", "mailbox": "sales@acme.test"} and "code_verifier" in exchanges[0]
    reads = db.scalars(
        select(Connection).where(
            Connection.org_id == org.id, Connection.zone == Zone.read, Connection.name == "sales@acme.test"
        )
    ).all()
    assert {c.kind for c in reads} == {ConnectionKind.gmail, ConnectionKind.gcal}
    stored = json.loads(secretbox.unseal(reads[0].secret_enc or ""))
    assert stored == {"refresh_token": "1//refresh", "client": "platform"}
    # Drafts: a separate grant (gmail.compose), sealed for the executor with the client it was made with.
    dq = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(cx.google_start(org, admin, "drafts")).query))
    assert dq["scope"] == "https://www.googleapis.com/auth/gmail.compose"
    dclient, _ = google_transport(scope="https://www.googleapis.com/auth/gmail.compose")
    cx.google_finish(db, str(admin.id), "code", dq["state"], client=dclient)
    drafts = db.scalars(
        select(Connection).where(Connection.org_id == org.id, Connection.zone == Zone.write)
    ).one()
    private = keys.executor_seal_private_key
    assert private is not None
    assert (
        json.loads(unseal(drafts.secret_sealed or "", private.get_secret_value()))["client_secret"]
        == "platform-secret"
    )
    # The same mailbox cannot feed a second organisation; a stale or forged state is refused.
    other, other_admin = company(db, "other")
    oq = dict(
        urllib.parse.parse_qsl(urllib.parse.urlparse(cx.google_start(other, other_admin, "read")).query)
    )
    with pytest.raises(cx.ConnectError, match="another Casefile organisation"):
        cx.google_finish(db, str(other_admin.id), "code", oq["state"], client=google_transport(scope=READ)[0])
    with pytest.raises(cx.ConnectError, match="not valid"):
        cx.google_finish(db, str(admin.id), "code", "forged", client=client)


def test_a_companys_own_google_app_is_used_by_reader_and_executor(
    db: Session, keys: ExecutorSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from casefile.connectors.registry import _google_credentials

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")  # overrides the developer's .env: no platform Google app
    get_settings.cache_clear()
    org, admin = company(db)
    with pytest.raises(cx.ConnectError, match="not set up on this server"):
        cx.google_start(org, admin, "read")
    with pytest.raises(cx.ConnectError, match="googleusercontent"):
        cx.set_own_google_client(db, org, admin, "nope", "secret-123456")
    cx.set_own_google_client(db, org, admin, "own.apps.googleusercontent.com", "own-secret-123")
    assert "own-secret" not in json.dumps(org.settings)
    url = cx.google_start(org, admin, "read")
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))["state"]
    client, exchanges = google_transport(scope=READ)
    cx.google_finish(db, str(admin.id), "code", state, client=client)
    assert exchanges[0]["client_secret"] == "own-secret-123"
    conn = db.scalars(
        select(Connection).where(Connection.org_id == org.id, Connection.kind == ConnectionKind.gmail)
    ).one()
    assert _google_credentials(conn, org) == (
        "own.apps.googleusercontent.com",
        "own-secret-123",
        "1//refresh",
    )


# ---------------------------------------------------------------- executor: one process, many companies


def test_each_company_gets_only_its_own_writers(db: Session, keys: ExecutorSettings) -> None:
    a, _ = company(db, "alpha")
    b, _ = company(db, "bravo")
    ca = ensure_connection(db, a, ConnectionKind.slack, name="workspace", zone=Zone.write)
    ca.secret_sealed = seal_for_executor("xoxb-alpha")
    cb = ensure_connection(db, b, ConnectionKind.hubspot, name="crm", zone=Zone.write)
    cb.secret_sealed = seal_for_executor("pat-bravo")
    broken = ensure_connection(db, b, ConnectionKind.slack, name="workspace", zone=Zone.write)
    broken.secret_sealed = seal_for_executor("xoxb-bravo", new_keypair()[1])  # sealed for another executor
    db.flush()
    wa = build_writer_set(db, a, keys)
    wb = build_writer_set(db, b, keys)
    assert sorted(wa.writers) == ["casefile", "slack"] and sorted(wb.writers) == ["casefile", "hubspot"]
    assert broken.id in wb.errors and "does not open" in wb.errors[broken.id]
    tenants = Tenants(db, keys)
    first = tenants.writers(a)
    assert tenants.writers(a) is first  # cached until a's write connections change
    ca.secret_sealed = seal_for_executor("xoxb-alpha-2")
    db.flush()
    assert tenants.writers(a) is not first
    tenants.heartbeat(b, shadow=False)
    assert broken.cursor["writer"] is False and "error" in broken.cursor and cb.cursor["writer"] is True


def test_system_scope_restores_a_system_mode_session(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tenant, "_process_system", True)
    tenant.system_scope(db)
    tenant.system_scope(db, False)
    assert tenant._values(db)["b"] == "on"  # still system after a helper's one-query widening


# ---------------------------------------------------------------- AI caps


def test_per_company_and_platform_ai_caps(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from casefile.ai.gateway import AiGateway

    org, _ = company(db, "capco", permitted=False)
    gateway = AiGateway(db, org.id, client=object())  # type: ignore[arg-type]
    assert gateway.daily_cap() == get_settings().ai_daily_budget_usd and not gateway.over_budget()
    org.settings = {**org.settings, "ai_daily_budget_usd": 0.5}
    db.add(
        LlmCall(
            org_id=org.id,
            created_at=datetime.now(UTC),
            purpose="t",
            model="m",
            prompt_version="t",
            cost_usd=0.6,
        )
    )
    db.flush()
    assert gateway.daily_cap() == 0.5 and gateway.over_budget()
    other, _ = company(db, "quietco", permitted=False)
    monkeypatch.setattr(get_settings(), "ai_platform_daily_budget_usd", 0.5)
    assert AiGateway(db, other.id, client=object()).over_budget()  # type: ignore[arg-type]


# ---------------------------------------------------------------- API end to end


@pytest.fixture
def api(db: Session, keys: ExecutorSettings) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def outbox_link(path: str) -> str:
    files = sorted(get_settings().outbox_dir.glob("*.eml"))
    text = files[-1].read_text()
    word = next(w for w in text.split() if f"/{path}/" in w)
    return word.rsplit("/", 1)[1]


def test_register_confirm_and_connect_through_the_api(api: TestClient, db: Session) -> None:
    assert api.get("/api/auth/signup-config").json() == {"enabled": True, "mail": "outbox"}
    body = {
        "company": "Globex",
        "name": "Hank",
        "email": "hank@globex.test",
        "password": PASSWORD,
        "timezone": "UTC",
    }
    r = api.post("/api/auth/register", json=body, headers=CSRF)
    assert r.status_code == 200 and r.json()["mail"] == "outbox"
    token = outbox_link("verify")
    assert api.get(f"/api/auth/verify/{token}").json()["company"] == "Globex"
    assert db.scalar(select(Org).where(Org.name == "Globex")) is None  # opening the link creates nothing
    me = api.post(f"/api/auth/verify/{token}", headers=CSRF).json()
    assert me["org_mode"] == "setup" and me["role"] == "admin" and me["org_name"] == "Globex"
    assert api.get("/api/auth/me").status_code == 200  # signed in by confirming
    ov = api.get("/api/connect").json()
    assert ov["server"]["can_seal_for_executor"] and ov["permission_recorded"] is False
    assert ov["slack_manifest"]["oauth_config"]["scopes"]["bot"]
    refused = api.post(
        "/api/connect/slack", json={"bot_token": "xoxb-1", "app_token": "xapp-1"}, headers=CSRF
    )
    assert refused.status_code == 422 and "data permission" in refused.json()["detail"]
    assert api.post("/api/auth/forgot", json={"email": "nobody@x.test"}, headers=CSRF).json() == {
        "sent": True
    }
    # Google sends the browser back here; without a valid state nothing is connected.
    back = api.get("/api/oauth/google/callback?error=access_denied", follow_redirects=False)
    assert back.status_code == 303 and "google_error=you%20cancelled" in back.headers["location"]


def test_disconnect_forgets_the_credential(db: Session, keys: ExecutorSettings) -> None:
    org, admin = company(db)
    conn = ensure_connection(db, org, ConnectionKind.hubspot, name="crm", zone=Zone.write)
    conn.secret_sealed = seal_for_executor("pat-x")
    db.flush()
    cx.disconnect(db, org, admin, conn.id)
    assert conn.secret_sealed is None and conn.status.value == "disabled"
    with pytest.raises(cx.ConnectError):
        cx.disconnect(db, org, admin, uuid.uuid4())


def test_an_expired_google_grant_asks_for_reconnect_and_the_executor_notices(
    db: Session, keys: ExecutorSettings
) -> None:
    from casefile.connectors.base import AuthError as GoogleAuthError

    org, _ = company(db, "expiry")
    read = ensure_connection(db, org, ConnectionKind.gmail, name="sales@expiry.test")
    read.last_error = "google token refresh failed: 400 {'error': 'invalid_grant'}"
    friendly, reconnect = cx.problem(read)
    assert reconnect and friendly is not None and "Reconnect" in friendly
    assert cx.problem(ensure_connection(db, org, ConnectionKind.hubspot, name="crm")) == (None, False)
    drafts = ensure_connection(db, org, ConnectionKind.gmail, name="drafts", zone=Zone.write)
    drafts.secret_sealed = seal_for_executor(
        json.dumps({"client_id": "c", "client_secret": "s", "refresh_token": "r"})
    )
    db.flush()
    tenants = Tenants(db, keys)
    ws = tenants.writers(org)
    assert "gmail" in ws.writers and drafts.id in ws.checks

    def expired() -> object:
        raise GoogleAuthError("google token refresh failed: 400 invalid_grant")

    ws.checks[drafts.id] = expired
    tenants.heartbeat(org, shadow=False)
    assert drafts.cursor["writer"] is False and "invalid_grant" in drafts.cursor["error"]
    view = next(v for v in cx.overview(db, org)["google"]["drafts"])
    assert view["needs_reconnect"] and view["executor"]["ready"] is False


def test_only_companies_with_a_deal_are_watched(db: Session) -> None:
    """A CRM also holds prospects, vendors and companies it created from newsletters: no deal, not a
    customer — not scored, and their mail is never searched for."""
    from casefile.cases.service import RecomputeContext, recompute_account
    from casefile.connectors.registry import DbReaderContext
    from casefile.db.models import Account, Contact, Deal

    org, _ = company(db, "watch", permitted=False)
    newsletter = Account(
        org_id=org.id, crm_source="t", crm_id="n", name="Newsletter Co", domains=["news.test"]
    )
    customer = Account(org_id=org.id, crm_source="t", crm_id="c", name="Customer Co", domains=["cust.test"])
    db.add_all([newsletter, customer])
    db.flush()
    db.add_all(
        [
            Contact(org_id=org.id, account_id=newsletter.id, email="digest@news.test", name="Digest"),
            Contact(org_id=org.id, account_id=customer.id, email="dana@cust.test", name="Dana"),
            Deal(org_id=org.id, account_id=customer.id, crm_source="t", crm_id="d1", name="Renewal"),
        ]
    )
    db.flush()
    ctx = DbReaderContext(db, org)
    assert ctx.customer_domains() == {"cust.test"} and ctx.known_emails() == {"dana@cust.test"}
    result = recompute_account(db, newsletter.id, RecomputeContext.build(db, org))
    assert result.status == "inactive"


def test_tasks_and_notes_attach_to_the_deal_so_a_deal_write_key_is_enough(db: Session) -> None:
    """Linking to a company needs company *write* access in HubSpot (observed): with a deal, attach
    there only; an account without a deal falls back to the company."""
    from casefile.actions.targets import hubspot_target
    from casefile.db.models import Account, Deal

    org, _ = company(db, "targets", permitted=False)
    ensure_connection(
        db, org, ConnectionKind.hubspot, name="crm", zone=Zone.write, config={"id_source": "crm_id"}
    )
    acc = Account(org_id=org.id, crm_source="hubspot", crm_id="111", name="Acme", domains=["acme.test"])
    db.add(acc)
    db.flush()
    deal = Deal(org_id=org.id, account_id=acc.id, crm_source="hubspot", crm_id="222", name="Renewal")
    db.add(deal)
    db.flush()
    with_deal = hubspot_target(db, acc, deal, None)
    assert (with_deal.company, with_deal.deal) == (None, "222")
    without = hubspot_target(db, acc, None, None)
    assert (without.company, without.deal) == ("111", None)
