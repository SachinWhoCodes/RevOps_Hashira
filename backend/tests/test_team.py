"""C1: staff logins — admin-managed team, one-time invite links, roles, switching a login off."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.api.deps import get_db
from casefile.api.main import app
from casefile.api.routers import team as team_router
from casefile.db.models import AppUser, AuditLog, Org, UserInvite
from casefile.mailer import Mail, MailError

CSRF = {"x-casefile-csrf": "1"}
ADMIN = {"email": "admin@lumora.test", "password": "test-password-1"}


@pytest.fixture(scope="module")
def client(synced: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: synced
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def as_admin(client: TestClient) -> None:
    client.cookies.clear()
    assert client.post("/api/auth/login", json=ADMIN, headers=CSRF).status_code == 200


def token_of(link: str) -> str:
    return link.rsplit("/invite/", 1)[1]


def test_admin_adds_and_invites_and_the_link_works_once(client: TestClient, synced: Session) -> None:
    as_admin(client)
    added = client.post(
        "/api/team", json={"email": "Nina@Lumora.test", "name": "Nina", "role": "owner"}, headers=CSRF
    )
    assert added.status_code == 200 and added.json()["status"] == "no_login"
    assert client.post("/api/team", json={"email": "nina@lumora.test"}, headers=CSRF).status_code == 409
    uid = added.json()["id"]
    first = client.post(f"/api/team/{uid}/invite", headers=CSRF).json()
    second = client.post(f"/api/team/{uid}/invite", headers=CSRF).json()
    assert first["link"] != second["link"]
    # Only a hash is stored, never the token.
    assert (
        synced.scalar(select(UserInvite.token_hash).where(UserInvite.token_hash == token_of(first["link"])))
        is None
    )
    client.cookies.clear()
    assert client.get(f"/api/auth/invite/{token_of(first['link'])}").status_code == 410  # replaced
    t = token_of(second["link"])
    assert client.get(f"/api/auth/invite/{t}").json()["email"] == "nina@lumora.test"
    assert client.post(f"/api/auth/invite/{t}", json={"password": "short"}, headers=CSRF).status_code == 422
    ok = client.post(f"/api/auth/invite/{t}", json={"password": "a-long-pass-phrase"}, headers=CSRF)
    assert ok.status_code == 200 and client.get("/api/auth/me").json()["email"] == "nina@lumora.test"
    assert (
        client.post(f"/api/auth/invite/{t}", json={"password": "another-long-one"}, headers=CSRF).status_code
        == 410
    )
    client.cookies.clear()
    login = client.post(
        "/api/auth/login", json={"email": "nina@lumora.test", "password": "a-long-pass-phrase"}, headers=CSRF
    )
    assert login.status_code == 200
    actions = set(synced.scalars(select(AuditLog.action).where(AuditLog.entity_id == uid)))
    assert {"user_added", "user_invited", "invite_accepted"} <= actions
    assert not any(
        "invite/" in str(d) for d in synced.scalars(select(AuditLog.details))
    )  # no token in the audit


class _Mailer:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[Mail] = []
        self.fail = fail

    def send(self, mail: Mail) -> str:
        if self.fail:
            raise MailError("provider refused")
        self.sent.append(mail)
        return "fake"


def test_an_emailed_invite_travels_only_in_the_email(
    client: TestClient, synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    as_admin(client)
    added = client.post("/api/team", json={"email": "mia@lumora.test", "name": "Mia"}, headers=CSRF)
    uid = added.json()["id"]
    down = _Mailer(fail=True)
    monkeypatch.setattr(team_router, "get_mailer", lambda: down)
    failed = client.post(f"/api/team/{uid}/invite", json={"send_email": True}, headers=CSRF)
    assert failed.status_code == 503
    # Nothing half-done: the invite that could not be sent was never stored.
    assert synced.scalars(select(UserInvite).where(UserInvite.user_id == uid)).all() == []
    mailer = _Mailer()
    monkeypatch.setattr(team_router, "get_mailer", lambda: mailer)
    sent = client.post(f"/api/team/{uid}/invite", json={"send_email": True}, headers=CSRF).json()
    assert sent["emailed"] is True and sent["link"] is None  # the admin never sees it
    (mail,) = mailer.sent
    assert mail.to == "mia@lumora.test" and "invited you" in mail.subject
    t = token_of(next(word for word in mail.text.split() if "/invite/" in word))
    client.cookies.clear()
    assert client.get(f"/api/auth/invite/{t}").json()["email"] == "mia@lumora.test"


def test_expired_and_unknown_links_are_refused(client: TestClient, synced: Session) -> None:
    as_admin(client)
    uid = client.post("/api/team", json={"email": "omar@lumora.test", "name": "Omar"}, headers=CSRF).json()[
        "id"
    ]
    link = client.post(f"/api/team/{uid}/invite", headers=CSRF).json()["link"]
    inv = synced.scalars(select(UserInvite).where(UserInvite.user_id == uid)).one()
    inv.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    synced.flush()
    client.cookies.clear()
    assert client.get(f"/api/auth/invite/{token_of(link)}").status_code == 410
    assert client.get("/api/auth/invite/not-a-real-token").status_code == 404


def test_only_admins_manage_the_team(client: TestClient, synced: Session) -> None:
    as_admin(client)
    uid = client.post("/api/team", json={"email": "lee@lumora.test", "name": "Lee"}, headers=CSRF).json()[
        "id"
    ]
    t = token_of(client.post(f"/api/team/{uid}/invite", headers=CSRF).json()["link"])
    client.cookies.clear()
    client.post(f"/api/auth/invite/{t}", json={"password": "lee-long-password"}, headers=CSRF)
    assert client.get("/api/team").status_code == 403
    assert client.post("/api/team", json={"email": "x@lumora.test"}, headers=CSRF).status_code == 403


def test_switching_a_login_off_ends_their_session(client: TestClient, synced: Session) -> None:
    as_admin(client)
    uid = client.post("/api/team", json={"email": "ravi@lumora.test", "name": "Ravi"}, headers=CSRF).json()[
        "id"
    ]
    t = token_of(client.post(f"/api/team/{uid}/invite", headers=CSRF).json()["link"])
    ravi = TestClient(app)
    ravi.post(f"/api/auth/invite/{t}", json={"password": "ravi-long-password"}, headers=CSRF)
    assert ravi.get("/api/auth/me").status_code == 200
    off = client.patch(f"/api/team/{uid}", json={"active": False}, headers=CSRF)
    assert off.status_code == 200 and off.json()["status"] == "no_login"
    assert ravi.get("/api/auth/me").status_code == 401
    assert client.patch(f"/api/team/{uid}", json={"role": "leader"}, headers=CSRF).json()["role"] == "leader"


def test_admin_cannot_lock_themselves_out_or_reach_another_org(client: TestClient, synced: Session) -> None:
    as_admin(client)
    me = client.get("/api/auth/me").json()
    assert client.patch(f"/api/team/{me['id']}", json={"role": "owner"}, headers=CSRF).status_code == 422
    assert client.patch(f"/api/team/{me['id']}", json={"active": False}, headers=CSRF).status_code == 422
    other = Org(slug="team-other", name="Other", timezone="UTC", internal_domains=[], settings={})
    synced.add(other)
    synced.flush()
    stranger = AppUser(org_id=other.id, email="s@other.test", name="S")
    synced.add(stranger)
    synced.flush()
    assert client.post(f"/api/team/{stranger.id}/invite", headers=CSRF).status_code == 404
    assert all(u["email"] != "s@other.test" for u in client.get("/api/team").json())
