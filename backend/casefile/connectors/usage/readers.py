"""Product usage readers. Expected columns (one row per customer per day, counts only):

    account_key   text    the product's id for the customer: a domain or the CRM company id
    date          date
    active_users  int
    key_actions   int
    seats_used    int     optional
    departments   json    optional, e.g. {"Sales": 12, "Ops": 4}

The last few days are re-read each run because analytics pipelines often land late data.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from casefile.connectors.base import ConnectorError, Page, RawItem

COLUMNS = ["account_key", "date", "active_users", "key_actions", "seats_used", "departments"]
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")
RELOAD_DAYS = 3


def _item(row: dict[str, Any]) -> RawItem:
    day = row["date"].isoformat() if isinstance(row["date"], date) else str(row["date"])[:10]
    departments = row.get("departments") or {}
    if isinstance(departments, str):
        departments = json.loads(departments) if departments.strip() else {}
    payload = {**{k: row.get(k) for k in COLUMNS}, "date": day, "departments": departments}
    return RawItem("usage.daily", f"{row['account_key']}:{day}", payload)


def _next_cursor(max_day: str | None, cursor: dict[str, Any]) -> dict[str, Any]:
    if not max_day:
        return dict(cursor)
    return {"since": (date.fromisoformat(max_day) - timedelta(days=RELOAD_DAYS)).isoformat()}


class UsageSqlReader:
    """Reads an aggregated view over a read-only connection (the session is forced read-only too)."""

    def __init__(self, dsn: str, view: str = "casefile_usage_daily", *, window_days: int = 730) -> None:
        if not IDENT.match(view):
            raise ConnectorError(f"usage: invalid view name {view!r}", retryable=False)
        self.dsn, self.view, self.window_days = dsn, view, window_days

    def pages(self, cursor: dict[str, Any]) -> Iterator[Page]:
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row

        since = cursor.get("since") or (date.today() - timedelta(days=self.window_days)).isoformat()
        query = sql.SQL("SELECT {cols} FROM {view} WHERE date >= %s ORDER BY date").format(
            cols=sql.SQL(", ").join(map(sql.Identifier, COLUMNS)),
            view=sql.Identifier(*self.view.split(".")),
        )
        max_day: str | None = None
        try:
            with (
                psycopg.connect(self.dsn, options="-c default_transaction_read_only=on") as conn,
                conn.cursor(name="casefile_usage", row_factory=dict_row) as cur,
            ):
                cur.execute(query, [since])
                while rows := cur.fetchmany(2000):
                    items = [_item(r) for r in rows]
                    max_day = items[-1].payload["date"]
                    yield Page(items, dict(cursor))
        except psycopg.Error as exc:
            raise ConnectorError(f"usage: {exc}") from exc
        yield Page([], _next_cursor(max_day, cursor))


class UsageCsvReader:
    """Reads a CSV export with the columns above (departments as a JSON string)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def pages(self, cursor: dict[str, Any]) -> Iterator[Page]:
        if not self.path.exists():
            raise ConnectorError(f"usage: file not found {self.path}", retryable=False)
        since = cursor.get("since", "")
        batch: list[RawItem] = []
        max_day: str | None = None
        with self.path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("date", "") < since:
                    continue
                item = _item(row)
                max_day = max(max_day or "", item.payload["date"])
                batch.append(item)
                if len(batch) == 2000:
                    yield Page(batch, dict(cursor))
                    batch = []
        yield Page(batch, _next_cursor(max_day, cursor))
