"""Gmail writer: creates drafts. There is no send path in this code (a test enforces it).

Scope: ``gmail.compose`` (create drafts). Each draft carries an ``X-Casefile-Ref`` header with the
item's marker, so a crash after the call can be reconciled by looking for the draft instead of
creating a second one. (We also set a Message-ID, but Gmail replaces it on drafts.)
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
from email.utils import formatdate
from typing import Any

import httpx

from casefile.connectors.google.auth import AccessToken
from casefile.connectors.google.gmail import BearerAuth
from casefile.executor.writers.base import ItemView, WriteRejected, WriteResult, read, send_once

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
RECENT_DRAFTS_TO_SCAN = 50


def message_id(item: ItemView) -> str:
    return f"<{item.marker}@casefile.local>"


def build_raw(item: ItemView, sender: str | None) -> str:
    msg = EmailMessage()
    p = item.payload
    msg["To"] = ", ".join(p["to"])
    if sender:
        msg["From"] = sender
    msg["Subject"] = p["subject"]
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = message_id(item)
    msg["X-Casefile-Ref"] = item.marker
    msg.set_content(p["body"])
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


class GmailDraftWriter:
    system = "gmail"

    def __init__(self, token: AccessToken | None = None, *, client: httpx.Client | None = None) -> None:
        if client is None:
            assert token is not None
            client = httpx.Client(base_url=BASE, timeout=30, auth=BearerAuth(token))
        self.client = client

    def write(self, item: ItemView) -> WriteResult:
        if item.action_type != "gmail_draft":
            raise WriteRejected(f"gmail writer cannot perform {item.action_type}")
        raw = build_raw(item, item.target_ref.get("mailbox"))
        data = send_once(self.client, "POST", "/drafts", "gmail draft", json={"message": {"raw": raw}}).json()
        message = data.get("message") or {}
        return WriteResult(
            str(data["id"]),
            {"object": "draft", "message_id": message.get("id"), "thread_id": message.get("threadId")},
        )

    def current(self, item: ItemView) -> dict[str, Any]:
        return {}

    def find(self, item: ItemView) -> WriteResult | None:
        """Look for our draft among the newest drafts by its X-Casefile-Ref header.

        Observed live (2026-10-11): Gmail replaces the Message-ID we set on a draft, so searching by
        it finds nothing, and ``messages.get`` is outside the ``gmail.compose`` scope. ``drafts.get``
        with ``format=metadata`` is allowed and returns our custom header unchanged.
        """
        recent = read(
            self.client, "GET", "/drafts", "gmail drafts", params={"maxResults": RECENT_DRAFTS_TO_SCAN}
        )
        for d in recent.get("drafts", []) or []:
            meta = read(
                self.client, "GET", f"/drafts/{d['id']}", "gmail draft", params={"format": "metadata"}
            )
            payload = (meta.get("message") or {}).get("payload") or {}
            headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
            if headers.get("x-casefile-ref") == item.marker:
                return WriteResult(str(d["id"]), {"object": "draft", "found_by": "X-Casefile-Ref"})
        return None
