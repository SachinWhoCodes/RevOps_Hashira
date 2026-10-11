"""Executor process (write zone). Run with ``python -m casefile.executor run``.

This is the only process that loads write credentials (``.env.executor``). It polls for approved
lines, reconciles crashed attempts at start-up and every few minutes, and sends the morning
digest. Commands:

- ``run``            the loop (``--shadow`` records would_execute instead of writing)
- ``once``           process the queue once and exit
- ``reconcile``      look for items stuck in "executing" and resolve them by marker
- ``sandbox-setup``  connect Slack/HubSpot/Gmail write tools and mirror open-case accounts
- ``digest``         send today's morning digest now (``--force`` ignores the 09:00 gate)
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import structlog
import typer
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from casefile.connectors.google.auth import AccessToken
from casefile.db.models import Connection, ConnectionKind, Org, Zone
from casefile.executor.digest import send_digests
from casefile.executor.runner import Executor
from casefile.executor.settings import ExecutorSettings, get_executor_settings
from casefile.executor.writers.base import Writer
from casefile.executor.writers.gmail import GmailDraftWriter
from casefile.executor.writers.hubspot import HubSpotWriter
from casefile.executor.writers.internal import InternalWriter
from casefile.executor.writers.slack import SlackWriter

log = structlog.get_logger()
app = typer.Typer(add_completion=False, no_args_is_help=True)


def _session(settings: ExecutorSettings) -> Session:
    return sessionmaker(create_engine(settings.database_url, future=True), expire_on_commit=False)()


def _org(session: Session, settings: ExecutorSettings) -> Org:
    org = session.scalar(select(Org).where(Org.slug == settings.org_slug))
    if org is None:
        raise typer.BadParameter(f"organisation {settings.org_slug!r} not found; run `casefile bootstrap`")
    return org


def _write_conn(session: Session, org: Org, kind: ConnectionKind) -> Connection | None:
    return session.scalar(
        select(Connection).where(
            Connection.org_id == org.id, Connection.kind == kind, Connection.zone == Zone.write
        )
    )


def build_writers(session: Session, org: Org, settings: ExecutorSettings) -> dict[str, Writer]:
    writers: dict[str, Writer] = {"casefile": InternalWriter()}
    if settings.write_hubspot_token and _write_conn(session, org, ConnectionKind.hubspot):
        read = settings.read_hubspot_token
        writers["hubspot"] = HubSpotWriter(
            settings.write_hubspot_token.get_secret_value(),
            read_token=read.get_secret_value() if read else None,
        )
    if settings.slack_bot_token and _write_conn(session, org, ConnectionKind.slack):
        writers["slack"] = SlackWriter(settings.slack_bot_token.get_secret_value())
    if (
        settings.write_google_refresh_token
        and settings.google_client_id
        and settings.google_client_secret
        and _write_conn(session, org, ConnectionKind.gmail)
    ):
        writers["gmail"] = GmailDraftWriter(
            AccessToken(
                settings.google_client_id,
                settings.google_client_secret.get_secret_value(),
                settings.write_google_refresh_token.get_secret_value(),
            )
        )
    return writers


def _heartbeat(session: Session, org: Org, shadow: bool, writers: dict[str, Writer]) -> None:
    now = datetime.now(UTC)
    for conn in session.scalars(
        select(Connection).where(Connection.org_id == org.id, Connection.zone == Zone.write)
    ):
        conn.last_success_at = now
        conn.cursor = {"heartbeat": now.isoformat(), "shadow": shadow, "writer": conn.kind.value in writers}
    session.commit()


@app.command()
def run(shadow: bool = typer.Option(None, help="Record would_execute instead of writing.")) -> None:
    """Poll for approved lines and execute them."""
    settings = get_executor_settings()
    session = _session(settings)
    org = _org(session, settings)
    shadow_mode = settings.executor_shadow_mode if shadow is None else shadow
    writers = build_writers(session, org, settings)
    executor = Executor(session, writers, shadow=shadow_mode)
    log.info("executor_started", writers=sorted(writers), shadow=shadow_mode)
    executor.reconcile(older_than=timedelta(seconds=0))
    last_reconcile = last_beat = last_digest = 0.0
    while True:
        executor.run_once()
        now = time.monotonic()
        if now - last_reconcile > 300:
            executor.reconcile(older_than=timedelta(seconds=settings.executor_reconcile_after_seconds))
            last_reconcile = now
        if now - last_beat > 30:
            _heartbeat(session, org, shadow_mode, writers)
            last_beat = now
        if now - last_digest > 60 and isinstance(writers.get("slack"), SlackWriter) and not shadow_mode:
            send_digests(
                session,
                org,
                writers["slack"],  # type: ignore[arg-type]
                base_url=settings.app_base_url,
                now=datetime.now(UTC),
                hour=settings.digest_hour_local,
            )
            last_digest = now
        time.sleep(settings.executor_poll_seconds)


@app.command()
def once(shadow: bool = typer.Option(None, help="Record would_execute instead of writing.")) -> None:
    """Process the queue once and print what happened."""
    settings = get_executor_settings()
    session = _session(settings)
    org = _org(session, settings)
    shadow_mode = settings.executor_shadow_mode if shadow is None else shadow
    writers = build_writers(session, org, settings)
    outcomes = Executor(session, writers, shadow=shadow_mode).run_once()
    _heartbeat(session, org, shadow_mode, writers)
    typer.echo(json.dumps([o.__dict__ for o in outcomes], indent=2, default=str))


@app.command()
def reconcile(older_than_seconds: int = 0) -> None:
    """Resolve lines stuck in 'executing' (after a crash) by searching for their marker."""
    settings = get_executor_settings()
    session = _session(settings)
    org = _org(session, settings)
    outcomes = Executor(session, build_writers(session, org, settings)).reconcile(
        older_than=timedelta(seconds=older_than_seconds)
    )
    typer.echo(json.dumps([o.__dict__ for o in outcomes], indent=2, default=str))


@app.command("sandbox-setup")
def sandbox_setup(
    mirror: bool = typer.Option(True, help="Copy open-case accounts into the HubSpot test portal."),
    limit: int = typer.Option(0, help="Mirror at most this many accounts (0 = all open cases)."),
    owner_id: str = typer.Option("", help="HubSpot owner ID to assign mirrored records and tasks to."),
) -> None:
    """Connect the write-zone test tools. Writes only to the sandbox HubSpot portal."""
    from casefile.executor.sandbox import mirror_open_cases, setup_gmail, setup_slack

    settings = get_executor_settings()
    session = _session(settings)
    org = _org(session, settings)
    out: dict[str, object] = {}
    if settings.slack_bot_token:
        out["slack"] = setup_slack(session, org, settings.slack_bot_token.get_secret_value())
        session.commit()
    if settings.write_google_refresh_token and settings.google_client_id and settings.google_client_secret:
        writer = GmailDraftWriter(
            AccessToken(
                settings.google_client_id,
                settings.google_client_secret.get_secret_value(),
                settings.write_google_refresh_token.get_secret_value(),
            )
        )
        out["gmail"] = setup_gmail(session, org, writer.client)
        session.commit()
    else:
        out["gmail"] = "skipped: run `uv run casefile google-auth --zone write` first"
    if settings.write_hubspot_token and mirror:
        report = mirror_open_cases(
            session,
            org,
            settings.write_hubspot_token.get_secret_value(),
            read_token=settings.read_hubspot_token.get_secret_value()
            if settings.read_hubspot_token
            else None,
            limit=limit or None,
            owner_id=owner_id or None,
        )
        out["hubspot"] = report.__dict__
    typer.echo(json.dumps(out, indent=2, default=str))


@app.command()
def digest(force: bool = typer.Option(False, help="Send even before the digest hour.")) -> None:
    """Send today's morning digest (once per owner per day)."""
    settings = get_executor_settings()
    session = _session(settings)
    org = _org(session, settings)
    writers = build_writers(session, org, settings)
    slack = writers.get("slack")
    if not isinstance(slack, SlackWriter):
        raise typer.BadParameter("no Slack write connection; run sandbox-setup first")
    sent = send_digests(
        session,
        org,
        slack,
        base_url=settings.app_base_url,
        now=datetime.now(UTC),
        hour=settings.digest_hour_local,
        force=force,
    )
    typer.echo(json.dumps(sent, indent=2))


if __name__ == "__main__":
    app()
