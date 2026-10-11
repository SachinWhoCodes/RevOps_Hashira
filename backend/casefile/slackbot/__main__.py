"""Slack approval listener (Phase 6.5): Socket Mode, app zone.

    uv run python -m casefile.slackbot run      # listen for button clicks
    uv run python -m casefile.slackbot check    # open one connection and report (no secrets printed)

It holds one secret, ``SLACK_APP_TOKEN`` (``xapp-…``, ``connections:write``), read from
``.env.slackbot`` only. That token can open a Socket Mode connection and receive events; it cannot
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
from casefile.db.models import Org
from casefile.db.session import get_sessionmaker

log = structlog.get_logger()
app = typer.Typer(help="Slack approval buttons (Socket Mode).")
ENV_FILE = REPO_ROOT / ".env.slackbot"
OPEN_URL = "https://slack.com/api/apps.connections.open"


def app_token() -> str:
    value = os.environ.get("SLACK_APP_TOKEN", "")
    if not value and ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            key, _, val = line.partition("=")
            if key.strip() == "SLACK_APP_TOKEN":
                value = val.strip().strip('"').strip("'")
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


def handle_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    session = get_sessionmaker()()
    try:
        # One organisation per deployment until Phase 8 maps Slack team ids to organisations.
        org = session.scalar(select(Org).where(Org.slug == get_settings().org_slug))
        if org is None:
            return None
        return handle_block_action(session, org, payload)
    finally:
        session.close()


async def listen(token: str) -> None:
    async with httpx.AsyncClient(timeout=30) as http:
        while True:
            url = await asyncio.to_thread(open_socket_url, token)
            async with websockets.connect(url) as ws:
                log.info("slackbot_connected")
                async for raw in ws:
                    env = json.loads(raw)
                    if env.get("type") == "disconnect":
                        log.info("slackbot_reconnecting", reason=env.get("reason"))
                        break
                    ack, work = process_envelope(env, handle_payload)
                    if ack:
                        await ws.send(json.dumps(ack))  # first, within Slack's 3 seconds
                    if work is None:
                        continue
                    reply = await asyncio.to_thread(work)
                    response_url = (env.get("payload") or {}).get("response_url")
                    if reply and response_url:
                        await http.post(response_url, json=reply)
                    log.info("slackbot_click", handled=reply is not None)


@app.command()
def run() -> None:
    """Listen for approval clicks (runs until stopped)."""
    asyncio.run(listen(app_token()))


@app.command()
def check() -> None:
    """Open one Socket Mode connection to prove the app token and Socket Mode work."""
    url = open_socket_url(app_token())
    typer.echo(f"socket mode ok ({'wss' if url.startswith('wss://') else 'unexpected'} url received)")


if __name__ == "__main__":
    app()
