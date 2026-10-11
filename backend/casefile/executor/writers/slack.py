"""Slack writer: a direct message from the Casefile bot to one person (Slack Web API).

Each message carries the item's marker in Slack message ``metadata``. Finding it again needs the
``im:history`` scope; without it ``find`` raises ``CannotVerify`` and the executor leaves the line
for a person to check instead of posting twice.
"""

from __future__ import annotations

from typing import Any

import httpx

from casefile.executor.writers.base import CannotVerify, ItemView, WriteRejected, WriteResult, send_once

BASE = "https://slack.com/api"
EVENT_TYPE = "casefile_change_item"


class SlackWriter:
    system = "slack"

    def __init__(self, token: str, *, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(
            base_url=BASE, timeout=30, headers={"Authorization": f"Bearer {token}"}
        )

    def _call(self, method: str, body: dict[str, Any], *, write: bool) -> dict[str, Any]:
        if write:
            response = send_once(self.client, "POST", f"/{method}", f"slack {method}", json=body)
        else:
            response = send_once(self.client, "GET", f"/{method}", f"slack {method}", params=body)
        data: dict[str, Any] = response.json()
        if not data.get("ok"):
            error = data.get("error", "unknown_error")
            if error == "missing_scope" and not write:
                raise CannotVerify(f"slack {method}: missing scope {data.get('needed')}")
            raise WriteRejected(
                f"slack {method}: {error}", retryable=error in {"ratelimited", "service_unavailable"}
            )
        return data

    def _channel(self, user_id: str) -> str:
        return str(self._call("conversations.open", {"users": user_id}, write=True)["channel"]["id"])

    def write(self, item: ItemView) -> WriteResult:
        if item.action_type != "slack_dm":
            raise WriteRejected(f"slack writer cannot perform {item.action_type}")
        user_id = item.target_ref.get("user_id")
        if not user_id:
            raise WriteRejected("no Slack user to message")
        channel = self._channel(str(user_id))
        data = self._call(
            "chat.postMessage",
            {
                "channel": channel,
                "text": item.payload["text"],
                "unfurl_links": False,
                "unfurl_media": False,
                "metadata": {"event_type": EVENT_TYPE, "event_payload": {"ref": item.marker}},
            },
            write=True,
        )
        return WriteResult(str(data["ts"]), {"object": "dm", "channel": channel})

    def current(self, item: ItemView) -> dict[str, Any]:
        return {}

    def find(self, item: ItemView) -> WriteResult | None:
        user_id = item.target_ref.get("user_id")
        if not user_id:
            return None
        channel = self._channel(str(user_id))
        data = self._call(
            "conversations.history",
            {"channel": channel, "limit": 50, "include_all_metadata": "true"},
            write=False,
        )
        for message in data.get("messages", []):
            payload = (message.get("metadata") or {}).get("event_payload") or {}
            if payload.get("ref") == item.marker:
                return WriteResult(
                    str(message["ts"]), {"object": "dm", "channel": channel, "found_by": "metadata"}
                )
        return None

    def post_text(self, user_id: str, text: str, blocks: list[dict[str, Any]] | None = None) -> str:
        """Internal notifications (morning digest) — not change items, still only from the executor.
        ``blocks`` may carry approval buttons; clicks go to the slackbot process, never here."""
        channel = self._channel(user_id)
        body: dict[str, Any] = {
            "channel": channel,
            "text": text,
            "unfurl_links": False,
            "unfurl_media": False,
        }
        if blocks:
            body["blocks"] = blocks
        data = self._call("chat.postMessage", body, write=True)
        return str(data["ts"])
