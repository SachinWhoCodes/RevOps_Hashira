"""Build the right reader for a connection row. Secrets come from read-zone settings only."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.config import REPO_ROOT, get_settings
from casefile.connectors.base import AuthError, ConnectorError, Reader
from casefile.connectors.fixture.reader import FixtureReader
from casefile.db.models import Account, Connection, ConnectionKind, Contact, Org, Zone
from casefile.ingest.util import field_mapping


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


def _secret(name: str | None) -> str:
    if not name:
        raise AuthError("connection has no secret_ref")
    value = getattr(get_settings(), name.lower(), None)
    secret = value.get_secret_value() if value is not None and hasattr(value, "get_secret_value") else value
    if not secret:
        raise AuthError(f"{name} is not set in the read-zone environment")
    return str(secret)


def build_reader(conn: Connection, session: Session) -> Reader:
    if conn.zone != Zone.read:
        raise ConnectorError("only read-zone connections can be synced", retryable=False)
    org = session.get(Org, conn.org_id)
    assert org is not None
    cfg = conn.config or {}
    if conn.kind == ConnectionKind.fixture:
        directory = Path(cfg.get("dir", "data/synth/default"))
        return FixtureReader(directory if directory.is_absolute() else REPO_ROOT / directory)
    if conn.kind == ConnectionKind.hubspot:
        from casefile.connectors.hubspot.reader import HubSpotReader

        return HubSpotReader(_secret(conn.secret_ref or "READ_HUBSPOT_TOKEN"), field_mapping())
    if conn.kind in (ConnectionKind.gmail, ConnectionKind.gcal):
        from casefile.connectors.google.auth import AccessToken
        from casefile.connectors.google.calendar import CalendarReader
        from casefile.connectors.google.gmail import GmailReader

        settings = get_settings()
        token = AccessToken(
            settings.google_client_id or _secret("GOOGLE_CLIENT_ID"),
            _secret("GOOGLE_CLIENT_SECRET"),
            _secret(conn.secret_ref or "READ_GOOGLE_REFRESH_TOKEN"),
        )
        ctx = DbReaderContext(session, org)
        window = int(cfg.get("window_days", 730))
        if conn.kind == ConnectionKind.gmail:
            return GmailReader(token, ctx, window_days=window)
        return CalendarReader(token, ctx, window_days=window)
    if conn.kind == ConnectionKind.usage_sql:
        from casefile.connectors.usage.readers import UsageSqlReader

        return UsageSqlReader(
            _secret(conn.secret_ref or "READ_USAGE_DSN"), cfg.get("view", "casefile_usage_daily")
        )
    if conn.kind == ConnectionKind.usage_csv:
        from casefile.connectors.usage.readers import UsageCsvReader

        path = Path(cfg["path"])
        return UsageCsvReader(path if path.is_absolute() else REPO_ROOT / path)
    raise ConnectorError(f"no reader for {conn.kind}", retryable=False)
