"""Build the right reader for a connection row. Secrets come from read-zone settings only."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.config import REPO_ROOT, get_settings
from casefile.connectors.base import AuthError, ConnectorError, Reader
from casefile.connectors.fixture.reader import FixtureReader
from casefile.db.models import Account, Connection, ConnectionKind, Contact, Deal, ExternalRef, Org, Zone


class DbReaderContext:
    """Answers readers' questions from our own database (what customers we know about)."""

    def __init__(self, session: Session, org: Org) -> None:
        self.session, self.org = session, org

    def customer_domains(self) -> set[str]:
        rows = self.session.scalars(select(Account.domains).where(Account.org_id == self.org.id))
        return {d.lower() for domains in rows for d in domains or []}

    def known_emails(self) -> set[str]:
        return set(self.session.scalars(select(Contact.email).where(Contact.org_id == self.org.id)))

    def internal_domains(self) -> set[str]:
        return {d.lower() for d in self.org.internal_domains}


def _env_secret(name: str) -> str:
    value = getattr(get_settings(), name.lower(), None)
    secret = value.get_secret_value() if value is not None and hasattr(value, "get_secret_value") else value
    if not secret:
        raise AuthError(f"{name} is not set in the read-zone environment")
    return str(secret)


def connection_secret(conn: Connection) -> str:
    """The credential for this connection: pasted in the app (stored encrypted) or named by the operator.

    There is deliberately no per-kind default: with several organisations, a connection without its own
    credential would otherwise read another company's CRM with the server-wide token.
    """
    if conn.secret_enc:
        from casefile.secretbox import SecretsUnavailable, unseal

        try:
            return unseal(conn.secret_enc)
        except SecretsUnavailable as exc:
            raise AuthError(str(exc)) from exc
    if conn.secret_ref:
        return _env_secret(conn.secret_ref)
    raise AuthError("this connection has no credential")


def _google_credentials(conn: Connection, org: Org) -> tuple[str, str, str]:
    """(client id, client secret, refresh token). A grant made in the app is stored as JSON naming the
    Google app it was made with (ours, or the company's own); an operator's grant is a bare token."""
    raw = connection_secret(conn)
    client = "platform"
    if raw.startswith("{"):
        data = json.loads(raw)
        raw, client = data["refresh_token"], data.get("client", "platform")
    if client == "own":
        from casefile.connect import own_google_client

        own = own_google_client(org)
        if own is None:
            raise AuthError("this mailbox was connected with the company's own Google app, which was removed")
        return own[0], own[1], raw
    settings = get_settings()
    return (
        settings.google_client_id or _env_secret("GOOGLE_CLIENT_ID"),
        _env_secret("GOOGLE_CLIENT_SECRET"),
        raw,
    )


def build_reader(conn: Connection, session: Session) -> Reader:
    if conn.zone != Zone.read:
        raise ConnectorError("only read-zone connections can be synced", retryable=False)
    org = session.get(Org, conn.org_id)
    assert org is not None
    cfg = conn.config or {}
    if conn.kind == ConnectionKind.fixture:
        directory = Path(cfg.get("dir", "data/synth/default"))
        return FixtureReader(directory if directory.is_absolute() else REPO_ROOT / directory)
    if conn.kind == ConnectionKind.hubspot and cfg.get("mode") == "sandbox_mirror":
        from casefile.connectors.hubspot.mirror import MirroredDeal, SandboxMirrorReader

        rows = session.execute(
            select(ExternalRef.external_id, Deal.crm_id, Account.crm_id)
            .join(Deal, Deal.id == ExternalRef.entity_id)
            .outerjoin(Account, Account.id == Deal.account_id)
            .where(
                ExternalRef.org_id == org.id,
                ExternalRef.entity_type == "deal",
                ExternalRef.system == "hubspot",
            )
        ).all()
        return SandboxMirrorReader(
            connection_secret(conn),
            [
                MirroredDeal(str(portal), str(local), str(company) if company else None)
                for portal, local, company in rows
            ],
        )
    if conn.kind == ConnectionKind.hubspot:
        from casefile.connectors.hubspot.reader import HubSpotReader
        from casefile.ingest.mapping import org_mapping

        return HubSpotReader(connection_secret(conn), org_mapping(session, org.id))
    if conn.kind in (ConnectionKind.gmail, ConnectionKind.gcal):
        from casefile.connectors.google.auth import AccessToken
        from casefile.connectors.google.calendar import CalendarReader
        from casefile.connectors.google.gmail import GmailReader

        token = AccessToken(*_google_credentials(conn, org))
        ctx = DbReaderContext(session, org)
        window = int(cfg.get("window_days", 730))
        if conn.kind == ConnectionKind.gmail:
            return GmailReader(token, ctx, window_days=window)
        return CalendarReader(token, ctx, window_days=window)
    if conn.kind == ConnectionKind.usage_sql:
        from casefile.connectors.usage.readers import UsageSqlReader

        return UsageSqlReader(connection_secret(conn), cfg.get("view", "casefile_usage_daily"))
    if conn.kind == ConnectionKind.usage_csv:
        from casefile.connectors.usage.readers import UsageCsvReader

        path = Path(cfg["path"])
        return UsageCsvReader(path if path.is_absolute() else REPO_ROOT / path)
    raise ConnectorError(f"no reader for {conn.kind}", retryable=False)
