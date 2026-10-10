from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dateutil import parser as dateparser

FREEMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.co.in",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "msn.com",
        "icloud.com",
        "me.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "rediffmail.com",
        "zoho.com",
        "zohomail.in",
        "yandex.com",
        "gmx.com",
        "mail.com",
    }
)
SYSTEM_LOCALPARTS = frozenset({"mailer-daemon", "postmaster", "no-reply", "noreply", "do-not-reply"})


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def parse_ts(value: Any) -> datetime | None:
    """HubSpot/Google timestamps: ISO 8601 strings or epoch-millisecond strings. Always UTC-aware."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value)
    if text.isdigit():
        return datetime.fromtimestamp(int(text) / 1000, tz=UTC)
    dt = dateparser.isoparse(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value)
    if len(text) == 10:
        return date.fromisoformat(text)
    ts = parse_ts(text)
    return ts.date() if ts else None


def parse_decimal_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return str(round(float(value), 2))
    except (TypeError, ValueError):
        return None


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def is_system_address(email: str) -> bool:
    return email.split("@", 1)[0].lower() in SYSTEM_LOCALPARTS


def chunks[T](items: Iterable[T], size: int) -> Iterator[list[T]]:
    batch: list[T] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


@lru_cache
def field_mapping() -> dict[str, Any]:
    return yaml.safe_load((Path(__file__).parent / "field_mapping.yaml").read_text())
