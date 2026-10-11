"""One executor process, many companies: each organisation gets its own writers, built from that
organisation's own credentials and used only for that organisation's lines (Executor(org_id=...)).

A credential is either sealed by the web app for this executor (``secret_sealed``, opened here with the
private key from ``.env.executor``) or named by the operator (``secret_ref``, an executor setting such as
``WRITE_HUBSPOT_TOKEN`` — how the sandbox organisation is wired). Nothing falls back to another
organisation's credential. Writers are rebuilt when an organisation's write connections change.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.connectors.base import AuthError
from casefile.connectors.google.auth import AccessToken
from casefile.connectors.hubspot.live import read_connection
from casefile.db.models import Connection, ConnectionKind, ConnectionStatus, Org, Zone
from casefile.executor.settings import ExecutorSettings
from casefile.executor.unseal import UnsealError, unseal
from casefile.executor.writers.base import Writer
from casefile.executor.writers.gmail import GmailDraftWriter
from casefile.executor.writers.hubspot import HubSpotWriter
from casefile.executor.writers.internal import InternalWriter
from casefile.executor.writers.slack import SlackWriter


@dataclass
class WriterSet:
    writers: dict[str, Writer]
    errors: dict[uuid.UUID, str] = field(default_factory=dict)  # per write connection, for the heartbeat
    fingerprint: str = ""
    # Cheap live checks run on each heartbeat (a Google grant can expire or be revoked at any time;
    # the access token is cached for an hour, so this reaches Google about once an hour).
    checks: dict[uuid.UUID, Callable[[], object]] = field(default_factory=dict)


def write_connections(session: Session, org_id: uuid.UUID) -> list[Connection]:
    return list(
        session.scalars(
            select(Connection)
            .where(
                Connection.org_id == org_id,
                Connection.zone == Zone.write,
                Connection.status != ConnectionStatus.disabled,
            )
            .order_by(Connection.created_at)
        )
    )


def fingerprint(conns: list[Connection], org: Org) -> str:
    parts = [
        [
            str(c.id),
            c.kind.value,
            c.secret_sealed or "",
            c.secret_ref or "",
            json.dumps(c.config or {}, sort_keys=True),
        ]
        for c in conns
    ]
    parts.append([json.dumps((org.settings or {}).get("google_client") or {}, sort_keys=True)])
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def _secret(conn: Connection, settings: ExecutorSettings) -> str:
    if conn.secret_sealed:
        key = settings.executor_seal_private_key
        if key is None or not key.get_secret_value():
            raise UnsealError("EXECUTOR_SEAL_PRIVATE_KEY is not set in .env.executor")
        return unseal(conn.secret_sealed, key.get_secret_value())
    if conn.secret_ref:
        value = getattr(settings, conn.secret_ref.lower(), None)
        secret = (
            value.get_secret_value() if value is not None and hasattr(value, "get_secret_value") else value
        )
        if secret:
            return str(secret)
        raise UnsealError(f"{conn.secret_ref} is not set in the executor's environment")
    raise UnsealError("no credential")


def _hubspot_read_token(session: Session, org_id: uuid.UUID) -> str | None:
    """Pre-write checks (current next step, marker search) use the organisation's own read token."""
    from casefile.connectors.registry import connection_secret

    conn = read_connection(session, org_id)
    if conn is None:
        return None
    try:
        return connection_secret(conn)
    except AuthError:
        return None


def build_writer_set(session: Session, org: Org, settings: ExecutorSettings) -> WriterSet:
    conns = write_connections(session, org.id)
    out = WriterSet({"casefile": InternalWriter()}, fingerprint=fingerprint(conns, org))
    for conn in conns:
        if conn.kind.value in out.writers:
            continue  # one writer per tool; the oldest connection wins (sandbox setups have one each)
        try:
            secret = _secret(conn, settings)
            if conn.kind == ConnectionKind.hubspot:
                out.writers["hubspot"] = HubSpotWriter(
                    secret, read_token=_hubspot_read_token(session, org.id)
                )
            elif conn.kind == ConnectionKind.slack:
                out.writers["slack"] = SlackWriter(secret)
            elif conn.kind == ConnectionKind.gmail:
                if secret.startswith("{"):
                    g = json.loads(secret)
                    token = AccessToken(g["client_id"], g["client_secret"], g["refresh_token"])
                else:  # operator-configured: the refresh token alone, our Google app from the environment
                    if not (settings.google_client_id and settings.google_client_secret):
                        raise UnsealError("GOOGLE_CLIENT_ID/SECRET are not set for the executor")
                    token = AccessToken(
                        settings.google_client_id, settings.google_client_secret.get_secret_value(), secret
                    )
                out.writers["gmail"] = GmailDraftWriter(token)
                out.checks[conn.id] = token.get
        except (UnsealError, ValueError, KeyError) as exc:
            out.errors[conn.id] = str(exc)[:300]
    return out


class Tenants:
    """Writer sets per organisation, rebuilt only when that organisation's write connections change."""

    def __init__(self, session: Session, settings: ExecutorSettings) -> None:
        self.session, self.settings = session, settings
        self._sets: dict[uuid.UUID, WriterSet] = {}

    def orgs(self) -> list[Org]:
        return list(self.session.scalars(select(Org).order_by(Org.created_at)))

    def writers(self, org: Org) -> WriterSet:
        current = fingerprint(write_connections(self.session, org.id), org)
        cached = self._sets.get(org.id)
        if cached is None or cached.fingerprint != current:
            cached = build_writer_set(self.session, org, self.settings)
            self._sets[org.id] = cached
        return cached

    def heartbeat(self, org: Org, shadow: bool) -> None:
        ws = self.writers(org)
        now = datetime.now(UTC).isoformat()
        for conn in write_connections(self.session, org.id):
            usable = conn.kind.value in ws.writers
            error = ws.errors.get(conn.id)
            if conn.id in ws.checks:
                try:
                    ws.checks[conn.id]()
                except AuthError as exc:
                    usable, error = False, str(exc)[:300]
            cursor: dict[str, Any] = {"heartbeat": now, "shadow": shadow, "writer": usable}
            if error:
                cursor["error"] = error
            conn.cursor = cursor
            conn.last_success_at = datetime.now(UTC)
        self.session.commit()
