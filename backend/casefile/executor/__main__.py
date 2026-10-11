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
from typing import Any

import structlog
import typer
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from casefile.connectors.google.auth import AccessToken
from casefile.db.models import Org
from casefile.executor.digest import send_digests
from casefile.executor.runner import Executor
from casefile.executor.settings import ExecutorSettings, get_executor_settings
from casefile.executor.tenants import Tenants
from casefile.executor.writers.gmail import GmailDraftWriter
from casefile.executor.writers.slack import SlackWriter

log = structlog.get_logger()
app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def _system_mode() -> None:
    """The executor writes for every organisation (row-level security bypass, by design)."""
    from casefile.db.tenant import set_process_system

    set_process_system()


def _session(settings: ExecutorSettings) -> Session:
    return sessionmaker(create_engine(settings.database_url, future=True), expire_on_commit=False)()


def _org(session: Session, settings: ExecutorSettings) -> Org:
    org = session.scalar(select(Org).where(Org.slug == settings.org_slug))
    if org is None:
        raise typer.BadParameter(f"organisation {settings.org_slug!r} not found; run `casefile bootstrap`")
    return org


def _run_org(tenants: Tenants, org: Org, shadow: bool) -> list[Any]:
    ws = tenants.writers(org)
    return Executor(tenants.session, ws.writers, org_id=org.id, shadow=shadow).run_once()


def _is_live(session: Session, org: Org) -> bool:
    """The morning digest is a Slack message, so it waits for live mode like every other write."""
    from casefile.onboarding import org_mode

    session.refresh(org)
    return org_mode(org) == "live"


@app.command()
def run(shadow: bool = typer.Option(None, help="Record would_execute instead of writing.")) -> None:
    """Poll for approved lines of every organisation and execute them with that organisation's writers."""
    settings = get_executor_settings()
    session = _session(settings)
    tenants = Tenants(session, settings)
    shadow_mode = settings.executor_shadow_mode if shadow is None else shadow
    log.info("executor_started", organisations=len(tenants.orgs()), shadow=shadow_mode)
    for org in tenants.orgs():
        ws = tenants.writers(org)
        Executor(session, ws.writers, org_id=org.id).reconcile(older_than=timedelta(seconds=0))
    last_reconcile = last_beat = last_digest = 0.0
    while True:
        orgs = tenants.orgs()
        for org in orgs:
            _run_org(tenants, org, shadow_mode)
        now = time.monotonic()
        if now - last_reconcile > 300:
            for org in orgs:
                ws = tenants.writers(org)
                Executor(session, ws.writers, org_id=org.id).reconcile(
                    older_than=timedelta(seconds=settings.executor_reconcile_after_seconds)
                )
            last_reconcile = now
        if now - last_beat > 30:
            for org in orgs:
                tenants.heartbeat(org, shadow_mode)
            last_beat = now
        if now - last_digest > 60 and not shadow_mode:
            for org in orgs:
                slack = tenants.writers(org).writers.get("slack")
                if isinstance(slack, SlackWriter) and _is_live(session, org):
                    send_digests(
                        session,
                        org,
                        slack,
                        base_url=settings.app_base_url,
                        now=datetime.now(UTC),
                        hour=settings.digest_hour_local,
                    )
            last_digest = now
        time.sleep(settings.executor_poll_seconds)


@app.command()
def once(shadow: bool = typer.Option(None, help="Record would_execute instead of writing.")) -> None:
    """Process every organisation's queue once and print what happened."""
    settings = get_executor_settings()
    session = _session(settings)
    tenants = Tenants(session, settings)
    shadow_mode = settings.executor_shadow_mode if shadow is None else shadow
    report: dict[str, Any] = {}
    for org in tenants.orgs():
        outcomes = _run_org(tenants, org, shadow_mode)
        tenants.heartbeat(org, shadow_mode)
        ws = tenants.writers(org)
        report[org.slug] = {
            "writers": sorted(ws.writers),
            "credential_errors": len(ws.errors),
            "outcomes": [o.__dict__ for o in outcomes],
        }
    typer.echo(json.dumps(report, indent=2, default=str))


@app.command()
def reconcile(older_than_seconds: int = 0) -> None:
    """Resolve lines stuck in 'executing' (after a crash) by searching for their marker."""
    settings = get_executor_settings()
    session = _session(settings)
    tenants = Tenants(session, settings)
    out: dict[str, Any] = {}
    for org in tenants.orgs():
        ws = tenants.writers(org)
        outcomes = Executor(session, ws.writers, org_id=org.id).reconcile(
            older_than=timedelta(seconds=older_than_seconds)
        )
        out[org.slug] = [o.__dict__ for o in outcomes]
    typer.echo(json.dumps(out, indent=2, default=str))


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
def digest(
    force: bool = typer.Option(False, help="Send even before the digest hour."),
    owner: str = typer.Option("", help="Only this owner's digest (email)."),
) -> None:
    """Send today's morning digest (once per owner per day)."""
    settings = get_executor_settings()
    session = _session(settings)
    org = _org(session, settings)
    slack = Tenants(session, settings).writers(org).writers.get("slack")
    if not isinstance(slack, SlackWriter):
        raise typer.BadParameter("no Slack write connection; run sandbox-setup first")
    if not _is_live(session, org):
        raise typer.BadParameter("the organisation is not live; the digest waits for live mode")
    sent = send_digests(
        session,
        org,
        slack,
        base_url=settings.app_base_url,
        now=datetime.now(UTC),
        hour=settings.digest_hour_local,
        force=force,
        owner_email=owner or None,
    )
    typer.echo(json.dumps(sent, indent=2))


if __name__ == "__main__":
    app()
