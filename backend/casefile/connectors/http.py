"""Shared HTTP behaviour for live connectors: retries with backoff, Retry-After on 429, clear errors."""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

from casefile.connectors.base import AuthError, ConnectorError, NotFound, RateLimited

log = structlog.get_logger()

MAX_ATTEMPTS = 5
MAX_RETRY_AFTER_SECONDS = 60.0


RATE_LIMIT_403 = ("rateLimitExceeded", "userRateLimitExceeded", "Quota exceeded", "RATE_LIMIT_EXCEEDED")


def _rate_limit_403(response: httpx.Response) -> bool:
    return any(marker in response.text for marker in RATE_LIMIT_403)


class HttpSource:
    def __init__(self, name: str, client: httpx.Client, *, sleep: Any = time.sleep) -> None:
        self.name = name
        self.client = client
        self._sleep = sleep

    def request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise ConnectorError(f"{self.name}: network error: {exc}") from exc
                self._sleep(min(2**attempt, 30))
                continue
            if response.status_code == 403 and _rate_limit_403(response):
                # Google reports per-user quotas as 403 (rateLimitExceeded, "Quota exceeded … per minute")
                # and asks for exponential backoff. Observed live on a real mailbox, 2026-10-11.
                if attempt == MAX_ATTEMPTS:
                    raise RateLimited(f"{self.name}: quota exceeded; try again in a minute", retry_after=60.0)
                wait = float(response.headers.get("Retry-After", 2**attempt))
                log.info("rate_limited", source=self.name, wait=wait, status=403)
                self._sleep(min(wait, MAX_RETRY_AFTER_SECONDS))
                continue
            if response.status_code in (401, 403):
                raise AuthError(
                    f"{self.name}: {response.status_code} {response.text[:200]}", status=response.status_code
                )
            if response.status_code in (404, 410):
                raise NotFound(f"{self.name}: {response.status_code} {response.text[:200]}")
            if response.status_code == 429:
                wait = float(response.headers.get("Retry-After", 2**attempt))
                if attempt == MAX_ATTEMPTS or wait > MAX_RETRY_AFTER_SECONDS:
                    raise RateLimited(f"{self.name}: rate limited", retry_after=wait)
                log.info("rate_limited", source=self.name, wait=wait)
                self._sleep(wait)
                continue
            if response.status_code >= 500:
                if attempt == MAX_ATTEMPTS:
                    raise ConnectorError(f"{self.name}: {response.status_code} after {attempt} attempts")
                self._sleep(min(2**attempt, 30))
                continue
            if response.status_code >= 400:
                raise ConnectorError(
                    f"{self.name}: {response.status_code} {response.text[:300]}", retryable=False
                )
            return response.json() if response.content else {}
        raise ConnectorError(f"{self.name}: exhausted retries")
