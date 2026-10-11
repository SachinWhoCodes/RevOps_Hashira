"""Self-service connections: an admin connects HubSpot, Google (Gmail + Calendar) and Slack from the app.

Where each credential lives (rule R1, ADR 0008):

| credential                         | stored as                                  | opened by           |
|------------------------------------|--------------------------------------------|---------------------|
| HubSpot read token                 | ``secret_enc`` (Fernet, SECRETS_KEY)        | worker, API checks  |
| HubSpot write token                | ``secret_sealed`` (executor public key)     | executor only       |
| Google read grant (gmail/calendar) | ``secret_enc`` JSON {refresh_token, client} | worker              |
| Google drafts grant (gmail.compose)| ``secret_sealed`` JSON {client, refresh}    | executor only       |
| Slack bot token (posts DMs)        | ``secret_sealed``                           | executor only       |
| Slack app token (receives clicks)  | ``secret_enc``                              | slackbot            |
| A company's own Google app secret  | org.settings: Fernet copy + sealed copy     | worker / executor   |

The web app sees a write credential once, while checking it, and keeps only the sealed copy. Every check
here is a real call that changes nothing (an empty HubSpot batch, Slack ``auth.test``, Google's profile).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile import sealing, secretbox
from casefile.audit.log import append as audit
from casefile.config import get_settings
from casefile.connectors.base import AuthError, ConnectorError
from casefile.connectors.google.auth import AUTH_URL, SCOPES, TOKEN_URL
from casefile.connectors.hubspot.live import HubSpotLive, read_connection
from casefile.db.models import AppUser, Connection, ConnectionKind, ConnectionStatus, Org, UserRole, Zone
from casefile.db.tenant import set_tenant, system_scope
from casefile.onboarding import OnboardingError, _require_permission
from casefile.worker.jobs import enqueue

SLACK_API = "https://slack.com/api"
SLACK_SCOPES = {"chat:write", "im:write", "users:read", "users:read.email"}
GOOGLE_PROFILE = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
GOOGLE_PURPOSES = {"read": SCOPES["read"], "drafts": SCOPES["write"]}
STATE_TTL_SECONDS = 600
HEARTBEAT_FRESH = timedelta(minutes=10)

SLACK_MANIFEST: dict[str, Any] = {
    "display_information": {
        "name": "Casefile",
        "description": "Revenue-risk cases, explained with evidence, approved by people.",
        "background_color": "#1f2937",
    },
    "features": {"bot_user": {"display_name": "Casefile", "always_online": False}},
    "oauth_config": {"scopes": {"bot": sorted(SLACK_SCOPES)}},
    "settings": {
        "interactivity": {"is_enabled": True},
        "socket_mode_enabled": True,
        "org_deploy_enabled": False,
        "token_rotation_enabled": False,
    },
}


class ConnectError(ValueError):
    """Shown to the admin as is."""


def _need_sealing() -> None:
    if not sealing.available():
        raise ConnectError(
            "this server cannot store write credentials yet (EXECUTOR_SEAL_PUBLIC_KEY); ask your operator"
        )


def _need_secrets() -> None:
    if not secretbox.available():
        raise ConnectError("this server cannot store credentials yet (SECRETS_KEY); ask your operator")


def _permitted(org: Org) -> None:
    try:
        _require_permission(org)
    except OnboardingError as exc:
        raise ConnectError("record the data permission on the Setup page first") from exc


def _token_shape(token: str, prefix: str = "", what: str = "token") -> str:
    token = token.strip()
    if not 10 <= len(token) <= 2000 or any(ch.isspace() for ch in token) or not token.startswith(prefix):
        raise ConnectError(
            f"that does not look like a {what}" + (f" (it starts with {prefix})" if prefix else "")
        )
    return token


def _upsert(
    session: Session, org: Org, kind: ConnectionKind, zone: Zone, name: str
) -> tuple[Connection, bool]:
    conn = session.scalar(
        select(Connection).where(
            Connection.org_id == org.id,
            Connection.kind == kind,
            Connection.zone == zone,
            Connection.name == name,
        )
    )
    if conn is not None:
        return conn, False
    conn = Connection(org_id=org.id, kind=kind, zone=zone, name=name, cursor={}, config={})
    session.add(conn)
    return conn, True


def _audit(
    session: Session, org: Org, user: AppUser, conn: Connection, created: bool, **details: Any
) -> None:
    session.flush()
    audit(
        session,
        org.id,
        "connection_added" if created else "connection_credential_replaced",
        actor_type="user",
        actor_id=str(user.id),
        entity_type="connection",
        entity_id=str(conn.id),
        details={
            "kind": conn.kind.value,
            "zone": conn.zone.value,
            "name": conn.name,
            **details,
        },  # never secrets
    )


def _elsewhere(session: Session, org: Org, kind: ConnectionKind, key: str, value: str) -> bool:
    """Is this external account (portal, Slack team, mailbox) already connected to another organisation?
    One explicit row-level-security bypass: the same account must never feed two companies."""
    system_scope(session)
    try:
        conns = session.scalars(
            select(Connection).where(Connection.kind == kind, Connection.org_id != org.id)
        ).all()
        return any(str((c.config or {}).get(key, "")).lower() == value.lower() for c in conns)
    finally:
        system_scope(session, False)


# ---------------------------------------------------------------- HubSpot write access


def connect_hubspot_write(
    session: Session, org: Org, user: AppUser, token: str, *, live: HubSpotLive | None = None
) -> dict[str, Any]:
    _permitted(org)
    _need_sealing()
    token = _token_shape(token, what="HubSpot access token")
    read = read_connection(session, org.id, include_mirror=False)
    if read is None or not (read.config or {}).get("portal_id"):
        raise ConnectError("connect the read-only HubSpot app first")
    live = live or HubSpotLive(token)
    try:
        portal = str(live.account()["portal_id"])
    except AuthError as exc:
        raise ConnectError(
            "HubSpot did not accept this token (revoked, mistyped, or from another app)"
        ) from exc
    except ConnectorError as exc:
        raise ConnectError(f"could not reach HubSpot: {exc}") from exc
    if portal != str(read.config["portal_id"]):
        raise ConnectError(
            f"this token is for portal {portal}, but your CRM is portal {read.config['portal_id']}"
        )
    access = live.can_write()
    missing = sorted(k for k, v in access.items() if v != "ok")
    if missing:
        names = {"tasks": "create tasks", "notes": "create notes", "deals": "update deals"}
        raise ConnectError("the token cannot " + ", ".join(names[m] for m in missing) + ". Add those scopes.")
    conn, created = _upsert(session, org, ConnectionKind.hubspot, Zone.write, "crm")
    conn.config = {"portal_id": portal, "id_source": "crm_id", "connected_by": user.email, "access": access}
    conn.secret_sealed, conn.secret_enc, conn.secret_ref = sealing.seal_for_executor(token), None, None
    conn.status = ConnectionStatus.active
    _audit(session, org, user, conn, created, portal_id=portal, access=access)
    return {"portal_id": portal, "access": access}


# ---------------------------------------------------------------- Slack


def _slack(token: str, method: str, client: httpx.Client | None, **params: Any) -> tuple[dict[str, Any], str]:
    c = client or httpx.Client(timeout=20)
    try:
        resp = c.post(f"{SLACK_API}/{method}", headers={"Authorization": f"Bearer {token}"}, data=params)
    except httpx.HTTPError as exc:
        raise ConnectError(f"could not reach Slack: {exc}") from exc
    return resp.json(), resp.headers.get("x-oauth-scopes", "")


def connect_slack(
    session: Session,
    org: Org,
    user: AppUser,
    bot_token: str,
    app_token: str,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    _permitted(org)
    _need_sealing()
    _need_secrets()
    bot_token = _token_shape(bot_token, "xoxb-", "Slack bot token")
    app_token = _token_shape(app_token, "xapp-", "Slack app-level token")
    who, scopes = _slack(bot_token, "auth.test", client)
    if not who.get("ok"):
        raise ConnectError(f"Slack did not accept the bot token ({who.get('error', 'unknown error')})")
    missing = sorted(SLACK_SCOPES - {s.strip() for s in scopes.split(",") if s.strip()})
    if missing:
        raise ConnectError("the bot is missing scopes: " + ", ".join(missing) + " (use the app manifest)")
    team_id = str(who.get("team_id"))
    if _elsewhere(session, org, ConnectionKind.slack, "team_id", team_id):
        raise ConnectError("this Slack workspace is already connected to another Casefile organisation")
    sock, _ = _slack(app_token, "apps.connections.open", client)
    if not sock.get("ok"):
        raise ConnectError(
            f"Slack did not accept the app-level token ({sock.get('error', 'unknown error')}); it needs the "
            "connections:write scope and Socket Mode switched on"
        )
    # Match Casefile users to Slack people by email (users:read.email), so DMs reach the right owner.
    linked, unmatched = 0, []
    me_slack: str | None = None
    for person in session.scalars(select(AppUser).where(AppUser.org_id == org.id)):
        found, _ = _slack(bot_token, "users.lookupByEmail", client, email=person.email)
        if found.get("ok"):
            person.slack_user_id = str(found["user"]["id"])
            linked += 1
            if person.id == user.id:
                me_slack = person.slack_user_id
        else:
            unmatched.append(person.email)
    conn, created = _upsert(session, org, ConnectionKind.slack, Zone.write, "workspace")
    conn.config = {
        "team_id": team_id,
        "team": who.get("team"),
        "bot_user": who.get("user_id"),
        # Owners not on Slack: their DMs go to the admin who connected Slack, marked "meant for <owner>".
        "fallback_user_id": me_slack,
        "linked_users": linked,
        "connected_by": user.email,
    }
    conn.secret_sealed = sealing.seal_for_executor(bot_token)  # posts DMs: executor only
    conn.secret_enc = secretbox.seal(app_token)  # receives button clicks: the slackbot process
    conn.secret_ref, conn.status = None, ConnectionStatus.active
    _audit(session, org, user, conn, created, team_id=team_id, linked_users=linked)
    return {"team": who.get("team"), "linked": linked, "unmatched": unmatched, "fallback": bool(me_slack)}


# ---------------------------------------------------------------- Google (Gmail + Calendar)


def redirect_uri() -> str:
    s = get_settings()
    return s.google_redirect_uri or f"{s.app_base_url.rstrip('/')}/api/oauth/google/callback"


def own_google_client(org: Org) -> tuple[str, str] | None:
    """The company's own Google app (client id, secret), opened with SECRETS_KEY; None if not set."""
    cfg = (org.settings or {}).get("google_client")
    if not cfg:
        return None
    return cfg["client_id"], secretbox.unseal(cfg["secret_enc"])


def google_client(org: Org) -> tuple[str, str, str]:
    """(client id, secret, "own" | "platform") for this organisation's Google sign-in."""
    own = own_google_client(org)
    if own:
        return own[0], own[1], "own"
    s = get_settings()
    if s.google_client_id and s.google_client_secret and s.google_client_secret.get_secret_value():
        return s.google_client_id, s.google_client_secret.get_secret_value(), "platform"
    raise ConnectError("Google sign-in is not set up on this server; add your own Google app below")


def set_own_google_client(
    session: Session, org: Org, user: AppUser, client_id: str, client_secret: str
) -> None:
    _permitted(org)
    _need_secrets()
    _need_sealing()
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id.endswith(".apps.googleusercontent.com") or any(c.isspace() for c in client_id):
        raise ConnectError("the client ID ends with .apps.googleusercontent.com")
    if not 10 <= len(client_secret) <= 200 or any(c.isspace() for c in client_secret):
        raise ConnectError("that does not look like a client secret")
    org.settings = {
        **(org.settings or {}),
        "google_client": {
            "client_id": client_id,
            "secret_enc": secretbox.seal(client_secret),  # worker: refreshes the read grant
            "secret_sealed": sealing.seal_for_executor(client_secret),  # executor: refreshes drafts grant
            "set_by": user.email,
            "at": datetime.now(UTC).isoformat(),
        },
    }
    audit(
        session,
        org.id,
        "google_client_set",
        actor_type="user",
        actor_id=str(user.id),
        entity_type="org",
        entity_id=str(org.id),
        details={"client_id": client_id},
    )


def clear_own_google_client(session: Session, org: Org, user: AppUser) -> None:
    org.settings = {k: v for k, v in (org.settings or {}).items() if k != "google_client"}
    audit(
        session,
        org.id,
        "google_client_cleared",
        actor_type="user",
        actor_id=str(user.id),
        entity_type="org",
        entity_id=str(org.id),
    )


def google_start(org: Org, user: AppUser, purpose: str) -> str:
    """The Google consent URL. State is encrypted (SECRETS_KEY), expires in 10 minutes, and names the
    user; the callback also requires that same user's session (no cross-account linking)."""
    _permitted(org)
    _need_secrets()
    if purpose not in GOOGLE_PURPOSES:
        raise ConnectError("unknown Google connection")
    if purpose == "drafts":
        _need_sealing()
    client_id, _, _ = google_client(org)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secretbox.seal(
        json.dumps({"o": str(org.id), "u": str(user.id), "p": purpose, "v": verifier, "t": int(time.time())})
    )
    return (
        AUTH_URL
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri(),
                "response_type": "code",
                "scope": " ".join(GOOGLE_PURPOSES[purpose]),
                "access_type": "offline",
                "prompt": "consent",  # always return a refresh token
                "include_granted_scopes": "false",  # keep read and drafts grants separate (two zones)
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
            }
        )
    )


def _state(raw: str) -> dict[str, Any]:
    try:
        data: dict[str, Any] = json.loads(secretbox.unseal(raw))
    except (secretbox.SecretsUnavailable, InvalidToken, ValueError) as exc:
        raise ConnectError("this Google sign-in link is not valid; start again from Connections") from exc
    if time.time() - int(data["t"]) > STATE_TTL_SECONDS:
        raise ConnectError("the Google sign-in took too long; start again from Connections")
    return data


def google_finish(
    session: Session,
    session_user_id: str | None,
    code: str,
    state: str,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    data = _state(state)
    if not session_user_id or session_user_id != data["u"]:
        raise ConnectError("sign in to Casefile as the person who started this connection, then try again")
    org_id = uuid.UUID(data["o"])
    set_tenant(session, org_id)
    org = session.get(Org, org_id)
    user = session.get(AppUser, uuid.UUID(data["u"]))
    if org is None or user is None or not user.is_active or user.role != UserRole.admin:
        raise ConnectError("only an admin of this organisation can connect Google")
    client_id, client_secret, source = google_client(org)
    http = client or httpx.Client(timeout=20)
    resp = http.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "code_verifier": data["v"],
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri(),
        },
    )
    if resp.status_code != 200:
        raise ConnectError(f"Google refused the sign-in ({resp.status_code}); try again")
    grant = resp.json()
    wanted = set(GOOGLE_PURPOSES[data["p"]])
    granted = set(str(grant.get("scope", "")).split())
    if not wanted <= granted:
        raise ConnectError(
            "Google did not grant every permission Casefile asked for (a box was unticked). Missing: "
            + ", ".join(sorted(s.rsplit("/", 1)[-1] for s in wanted - granted))
        )
    refresh = grant.get("refresh_token")
    if not refresh:
        raise ConnectError(
            "Google returned no lasting access; remove Casefile at myaccount.google.com and retry"
        )
    profile = http.get(GOOGLE_PROFILE, headers={"Authorization": f"Bearer {grant['access_token']}"})
    if profile.status_code != 200:
        raise ConnectError("connected, but could not read which mailbox this is; try again")
    mailbox = str(profile.json()["emailAddress"]).lower()
    if _elsewhere(session, org, ConnectionKind.gmail, "mailbox", mailbox):
        raise ConnectError(f"{mailbox} is already connected to another Casefile organisation")
    if data["p"] == "read":
        secret = secretbox.seal(json.dumps({"refresh_token": refresh, "client": source}))
        for kind in (ConnectionKind.gmail, ConnectionKind.gcal):
            conn, created = _upsert(session, org, kind, Zone.read, mailbox)
            conn.config = {"mailbox": mailbox, "client": source, "connected_by": user.email}
            conn.secret_enc, conn.secret_ref, conn.status = secret, None, ConnectionStatus.active
            conn.interval_minutes = 15 if kind == ConnectionKind.gmail else 30
            _audit(session, org, user, conn, created, mailbox=mailbox, client=source)
            enqueue(
                session,
                "sync_connection",
                {"connection_id": str(conn.id)},
                org_id=org.id,
                dedupe_key=f"sync:{conn.id}",
            )
    else:
        sealed = sealing.seal_for_executor(
            json.dumps({"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh})
        )
        conn, created = _upsert(session, org, ConnectionKind.gmail, Zone.write, "drafts")
        conn.config = {"mailbox": mailbox, "client": source, "connected_by": user.email}
        conn.secret_sealed, conn.secret_enc, conn.secret_ref = sealed, None, None
        conn.status = ConnectionStatus.active
        _audit(session, org, user, conn, created, mailbox=mailbox, client=source, drafts_only=True)
    return {"purpose": data["p"], "mailbox": mailbox}


# ---------------------------------------------------------------- disconnect and overview


def disconnect(session: Session, org: Org, user: AppUser, connection_id: uuid.UUID) -> None:
    """Stop using a connection and forget its credential. Data already synced is kept."""
    conn = session.scalar(
        select(Connection).where(Connection.id == connection_id, Connection.org_id == org.id)
    )
    if (
        conn is None
        or (conn.config or {}).get("mode") == "sandbox_mirror"
        or conn.kind == ConnectionKind.fixture
    ):
        raise ConnectError("connection not found")
    targets = [conn]
    if conn.kind in (ConnectionKind.gmail, ConnectionKind.gcal) and conn.zone == Zone.read:
        targets = list(
            session.scalars(
                select(Connection).where(
                    Connection.org_id == org.id,
                    Connection.zone == Zone.read,
                    Connection.name == conn.name,
                    Connection.kind.in_([ConnectionKind.gmail, ConnectionKind.gcal]),
                )
            )
        )
    for c in targets:
        c.status, c.secret_enc, c.secret_sealed = ConnectionStatus.disabled, None, None
        if c.secret_ref and c.zone == Zone.write:
            c.secret_ref = None
        session.flush()
        audit(
            session,
            org.id,
            "connection_disconnected",
            actor_type="user",
            actor_id=str(user.id),
            entity_type="connection",
            entity_id=str(c.id),
            details={"kind": c.kind.value, "zone": c.zone.value, "name": c.name},
        )


GOOGLE_EXPIRED = ("invalid_grant", "token refresh failed", "Token has been expired or revoked")


def problem(conn: Connection) -> tuple[str | None, bool]:
    """(plain-language problem, whether reconnecting fixes it) for a connection's last error."""
    err = f"{conn.last_error or ''} {(conn.cursor or {}).get('error') or ''}".strip()
    if not err:
        return None, False
    if conn.kind in (ConnectionKind.gmail, ConnectionKind.gcal) and any(m in err for m in GOOGLE_EXPIRED):
        return (
            "Google access expired or was removed (while Casefile's Google app is in testing, a connection "
            "lasts 7 days). Reconnect to resume; nothing synced so far is lost.",
            True,
        )
    if "401" in err or "invalid_auth" in err or "did not accept" in err:
        return "The tool no longer accepts this credential (revoked or rotated). Paste a new one.", False
    return None, False


def _view(conn: Connection, now: datetime) -> dict[str, Any]:
    cursor = conn.cursor or {}
    friendly, reconnect = problem(conn)
    beat = cursor.get("heartbeat")
    fresh = bool(beat and now - datetime.fromisoformat(beat) <= HEARTBEAT_FRESH)
    return {
        "id": str(conn.id),
        "kind": conn.kind.value,
        "zone": conn.zone.value,
        "name": conn.name,
        "status": conn.status.value,
        "config": {k: v for k, v in (conn.config or {}).items() if k not in ("path",)},
        "has_credential": bool(conn.secret_enc or conn.secret_sealed or conn.secret_ref),
        "credential": "sealed for the executor"
        if conn.secret_sealed
        else "encrypted"
        if conn.secret_enc
        else "server environment"
        if conn.secret_ref
        else "none",
        "last_success_at": conn.last_success_at.isoformat() if conn.last_success_at else None,
        "last_error": conn.last_error,
        "problem": friendly,
        "needs_reconnect": reconnect,
        # Write connections: what the executor reported on its last heartbeat (it alone can open them).
        "executor": None
        if conn.zone != Zone.write
        else {"seen": fresh, "ready": fresh and bool(cursor.get("writer")), "error": cursor.get("error")},
    }


def overview(session: Session, org: Org) -> dict[str, Any]:
    now = datetime.now(UTC)
    conns = session.scalars(
        select(Connection).where(Connection.org_id == org.id).order_by(Connection.kind, Connection.zone)
    ).all()

    def pick(kind: ConnectionKind, zone: Zone) -> list[dict[str, Any]]:
        return [
            _view(c, now)
            for c in conns
            if c.kind == kind
            and c.zone == zone
            and c.status != ConnectionStatus.disabled
            and (c.config or {}).get("mode") != "sandbox_mirror"
        ]

    s = get_settings()
    own = (org.settings or {}).get("google_client")
    mailboxes: dict[str, dict[str, Any]] = {}
    for v in pick(ConnectionKind.gmail, Zone.read):
        mailboxes.setdefault(v["name"], {"mailbox": v["name"]})["gmail"] = v
    for v in pick(ConnectionKind.gcal, Zone.read):
        mailboxes.setdefault(v["name"], {"mailbox": v["name"]})["calendar"] = v
    return {
        "server": {
            "can_store_credentials": secretbox.available(),
            "can_seal_for_executor": sealing.available(),
            "google_platform_app": bool(s.google_client_id and s.google_client_secret),
            "google_redirect_uri": redirect_uri(),
        },
        "permission_recorded": bool((org.settings or {}).get("data_permission")),
        "hubspot": {
            "read": pick(ConnectionKind.hubspot, Zone.read),
            "write": pick(ConnectionKind.hubspot, Zone.write),
        },
        "google": {
            "client": "own" if own else ("platform" if s.google_client_id else "none"),
            "own_client_id": own.get("client_id") if own else None,
            "mailboxes": list(mailboxes.values()),
            "drafts": pick(ConnectionKind.gmail, Zone.write),
        },
        "slack": pick(ConnectionKind.slack, Zone.write),
        "usage": pick(ConnectionKind.usage_csv, Zone.read) + pick(ConnectionKind.usage_sql, Zone.read),
        "synthetic": [_view(c, now) for c in conns if c.kind == ConnectionKind.fixture],
        "slack_manifest": SLACK_MANIFEST,
    }
