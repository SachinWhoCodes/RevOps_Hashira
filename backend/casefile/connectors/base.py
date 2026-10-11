"""Connector contracts.

A ``Reader`` yields pages of raw items in the source's own shape. After each page is stored and
normalised, the sync runner persists the page's cursor, so an interrupted sync resumes where it
stopped instead of starting over.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class RawItem:
    object_type: str  # e.g. "hubspot.company", "gmail.message", "gcal.event", "usage.daily"
    external_id: str
    payload: dict[str, Any]


@dataclass
class Page:
    items: list[RawItem]
    cursor: dict[str, Any] = field(default_factory=dict)


class ReaderContext(Protocol):
    """What a reader may ask the system, to fetch only what it needs (data minimisation)."""

    def customer_domains(self) -> set[str]: ...

    def known_emails(self) -> set[str]: ...

    def internal_domains(self) -> set[str]: ...


class Reader(Protocol):
    def pages(self, cursor: dict[str, Any]) -> Iterator[Page]: ...


class ConnectorError(Exception):
    """A source refused or failed. ``retryable`` decides whether the next scheduled run retries."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class AuthError(ConnectorError):
    """401 (token invalid or revoked) or 403 (token valid but lacks a scope)."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message, retryable=False)
        self.status = status


class NotFound(ConnectorError):
    """404/410 — e.g. an expired Gmail history id or Calendar sync token: fall back to a full sync."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


class RateLimited(ConnectorError):
    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message, retryable=True)
        self.retry_after = retry_after
