"""Writer contract. A writer performs exactly one approved change in one tool.

Outcomes are explicit because "did it happen?" decides whether a retry could duplicate:

- ``WriteResult``: done; carries the external ID.
- ``WriteRejected``: the tool refused or never received the request. Nothing was written.
  ``retryable`` says whether trying again later could succeed (rate limit, connect error).
- ``WriteUnknown``: the request may have been processed (timeout after sending, 5xx). The executor
  then searches for the item's marker before deciding anything.
- ``CannotVerify``: raised by ``find`` when the tool gives us no way to search (e.g. Slack without
  the history scope). The line is then left for a person to check; it is never resent blindly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class ItemView:
    id: uuid.UUID
    action_type: str
    target_ref: dict[str, Any]
    before: dict[str, Any]
    payload: dict[str, Any]
    marker: str


@dataclass(frozen=True)
class WriteResult:
    external_id: str | None
    details: dict[str, Any] = field(default_factory=dict)


class WriteRejected(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class WriteUnknown(Exception):
    pass


class CannotVerify(Exception):
    pass


class Writer(Protocol):
    system: str

    def write(self, item: ItemView) -> WriteResult: ...

    def find(self, item: ItemView) -> WriteResult | None: ...

    def current(self, item: ItemView) -> dict[str, Any]: ...


def send_once(client: httpx.Client, method: str, url: str, name: str, **kwargs: Any) -> httpx.Response:
    """One HTTP attempt with outcome classification. Writes are never retried blindly here."""
    try:
        response = client.request(method, url, **kwargs)
    except httpx.ConnectError as exc:  # never reached the server
        raise WriteRejected(f"{name}: could not connect: {exc}", retryable=True) from exc
    except httpx.TransportError as exc:  # sent, but no answer: it may have been processed
        raise WriteUnknown(f"{name}: no response: {exc}") from exc
    if response.status_code == 429:
        raise WriteRejected(f"{name}: rate limited", retryable=True)
    if response.status_code >= 500:
        raise WriteUnknown(f"{name}: server error {response.status_code}")
    if response.status_code in (401, 403):
        raise WriteRejected(f"{name}: not authorised ({response.status_code}): {response.text[:200]}")
    if response.status_code >= 400:
        raise WriteRejected(f"{name}: {response.status_code} {response.text[:300]}")
    return response


def read(client: httpx.Client, method: str, url: str, name: str, **kwargs: Any) -> dict[str, Any]:
    """Reads (searches, current values) may be retried freely; failures surface as WriteUnknown."""
    last: Exception | None = None
    for _ in range(3):
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            last = exc
            continue
        if response.status_code == 429 or response.status_code >= 500:
            last = RuntimeError(f"{response.status_code}")
            continue
        if response.status_code >= 400:
            raise WriteRejected(f"{name}: {response.status_code} {response.text[:300]}")
        return response.json() if response.content else {}
    raise WriteUnknown(f"{name}: read failed: {last}")
