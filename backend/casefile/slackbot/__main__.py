"""Slack approval listener (Phase 6.5): Socket Mode, app zone.

    uv run python -m casefile.slackbot run      # listen for button clicks
    uv run python -m casefile.slackbot check    # open one connection and report (no secrets printed)

It listens to every workspace connected in the app (each organisation's ``xapp-…`` token, encrypted
with SECRETS_KEY) plus the operator's ``SLACK_APP_TOKEN`` from ``.env.slackbot`` for the sandbox
organisation, one Socket Mode connection each; a click is handled only for the organisation whose
workspace it came from. That token can open a Socket Mode connection and receive events; it cannot
post messages or touch any other tool. Answers go back through each click's ``response_url``, which
needs no token. Decisions are recorded with the same code as the web app (``approve_item``,
``channel=slack``, audited); the separate executor still performs every write (rule R1).

Each event is acknowledged on the socket before it is handled (Slack retries anything not
acknowledged within 3 seconds, which must never record a decision twice).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from typing import Any

import httpx
import structlog
import typer
import websockets
from sqlalchemy import select

from casefile.actions.slack_approval import handle_block_action
from casefile.config import REPO_ROOT, get_settings
from casefile.db.models import Connection, ConnectionKind, ConnectionStatus, Org, Zone
from casefile.db.session import get_sessionmaker
from casefile.db.tenant import set_tenant, system_scope

log = structlog.get_logger()
app = typer.Typer(help="Slack approval buttons (Socket Mode).")
ENV_FILE = REPO_ROOT / ".env.slackbot"
OPEN_URL = "https://slack.com/api/apps.connections.open"


def env_app_token() -> str:
    """The operator's token (sandbox organisation), if any."""
    value = os.environ.get("SLACK_APP_TOKEN", "")
    if not value and ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            key, _, val = line.partition("=")
            if key.strip() == "SLACK_APP_TOKEN":
                value = val.strip().strip('"').strip("'")
    return value


def app_token() -> str:
    value = env_app_token()
    if not value:
        raise typer.BadParameter(f"SLACK_APP_TOKEN is not set in {ENV_FILE.name}")
    return value


def open_socket_url(token: str, client: httpx.Client | None = None) -> str:
    c = client or httpx.Client(timeout=30)
    data = c.post(OPEN_URL, headers={"Authorization": f"Bearer {token}"}).json()
    if not data.get("ok"):
        raise RuntimeError(f"apps.connections.open failed: {data.get('error')}")
    return str(data["url"])


def process_envelope(
    env: dict[str, Any], handler: Callable[[dict[str, Any]], dict[str, Any] | None]
) -> tuple[dict[str, Any] | None, Callable[[], dict[str, Any] | None] | None]:
    """(ack to send now, work to do after acking). Only button clicks are work."""
    ack = {"envelope_id": env["envelope_id"]} if env.get("envelope_id") else None
    payload = env.get("payload") or {}
    if env.get("type") == "interactive" and payload.get("type") == "block_actions":
        return ack, lambda: handler(payload)
    return ack, None


def handle_payload(org_id: Any, payload: dict[str, Any]) -> dict[str, Any] | None:
    session = get_sessionmaker()()
    try:
        set_tenant(session, org_id)  # row-level security: the click only sees its own organisation
        org = session.get(Org, org_id)
        if org is None:
            return None
        team = (payload.get("team") or {}).get("id")
        conn = session.scalar(
            select(Connection).where(
                Connection.org_id == org_id,
                Connection.kind == ConnectionKind.slack,
                Connection.zone == Zone.write,
            )
        )
        expected = (conn.config or {}).get("team_id") if conn else None
        if team and expected and team != expected:
            log.warning("slackbot_team_mismatch")  # a click from another workspace never touches this org
            return None
        return handle_block_action(session, org, payload)
    finally:
        session.close()


def workspaces() -> dict[str, tuple[Any, str]]:
    """{connection key: (organisation id, app token)} for every organisation with Slack connected in the
    app (token encrypted with SECRETS_KEY), plus the operator's token from .env.slackbot for ORG_SLUG."""
    from casefile.secretbox import SecretsUnavailable, unseal

    session = get_sessionmaker()()
    out: dict[str, tuple[Any, str]] = {}
    try:
        system_scope(session)  # the listener serves every organisation (one socket per workspace)
        for conn in session.scalars(
            select(Connection).where(
                Connection.kind == ConnectionKind.slack,
                Connection.zone == Zone.write,
                Connection.status != ConnectionStatus.disabled,
                Connection.secret_enc.is_not(None),
            )
        ):
            try:
                out[f"{conn.id}:{conn.secret_enc}"] = (conn.org_id, unseal(conn.secret_enc or ""))
            except SecretsUnavailable:
                log.warning("slackbot_token_unreadable", connection=str(conn.id))
        org = session.scalar(select(Org).where(Org.slug == get_settings().org_slug))
        token = env_app_token()
        if org is not None and token and not any(o == org.id for o, _ in out.values()):
            out[f"env:{org.id}"] = (org.id, token)
    finally:
        session.close()
    return out


async def listen(org_id: Any, token: str) -> None:
    async with httpx.AsyncClient(timeout=30) as http:
        while True:
            try:
                url = await asyncio.to_thread(open_socket_url, token)
            except RuntimeError as exc:
                log.warning("slackbot_open_failed", error=str(exc))
                await asyncio.sleep(60)
                continue
            async with websockets.connect(url) as ws:
                log.info("slackbot_connected", org=str(org_id))
                async for raw in ws:
                    env = json.loads(raw)
                    if env.get("type") == "disconnect":
                        log.info("slackbot_reconnecting", reason=env.get("reason"))
                        break
                    ack, work = process_envelope(env, lambda p: handle_payload(org_id, p))
                    if ack:
                        await ws.send(json.dumps(ack))  # first, within Slack's 3 seconds
                    if work is None:
                        continue
                    reply = await asyncio.to_thread(work)
                    response_url = (env.get("payload") or {}).get("response_url")
                    if reply and response_url:
                        await http.post(response_url, json=reply)
                    log.info("slackbot_click", handled=reply is not None)


async def supervise(refresh_seconds: int = 60) -> None:
    """One listener per connected workspace; started, stopped or restarted as admins change Slack."""
    tasks: dict[str, asyncio.Task[None]] = {}
    while True:
        wanted = await asyncio.to_thread(workspaces)
        for key in list(tasks):
            if key not in wanted:
                tasks.pop(key).cancel()
        for key, (org_id, token) in wanted.items():
            if key not in tasks or tasks[key].done():
                tasks[key] = asyncio.create_task(listen(org_id, token))
        log.info("slackbot_workspaces", count=len(tasks))
        await asyncio.sleep(refresh_seconds)


@app.command()
def run() -> None:
    """Listen for approval clicks from every connected workspace (runs until stopped)."""
    asyncio.run(supervise())


@app.command()
def check() -> None:
    """Open one Socket Mode connection to prove the app token and Socket Mode work."""
    url = open_socket_url(app_token())
    typer.echo(f"socket mode ok ({'wss' if url.startswith('wss://') else 'unexpected'} url received)")


if __name__ == "__main__":
    app()
