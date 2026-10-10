"""Product usage rows → usage_daily. Account resolution happens in the identity pass."""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert

from casefile.connectors.base import RawItem
from casefile.db.models import UsageDaily
from casefile.ingest.context import NormalizeContext
from casefile.ingest.util import chunks, parse_date


def _int(value: object) -> int:
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def rows(ctx: NormalizeContext, items: list[RawItem]) -> None:
    out = []
    for item in items:
        p = item.payload
        day = parse_date(p.get("date"))
        key = str(p.get("account_key") or "").strip().lower()
        if day is None or not key:
            continue
        seats = p.get("seats_used")
        out.append(
            {
                "org_id": ctx.org_id,
                "external_account_key": key,
                "date": day,
                "active_users": _int(p.get("active_users")),
                "key_actions": _int(p.get("key_actions")),
                "seats_used": _int(seats) if seats not in (None, "") else None,
                "departments": p.get("departments") or {},
                "source": item.object_type.split(".", 1)[0],
            }
        )
    unique = {(r["external_account_key"], r["date"]): r for r in out}  # one upsert per key per statement
    for batch in chunks(list(unique.values()), 1000):
        stmt = insert(UsageDaily).values(batch)
        ctx.session.execute(
            stmt.on_conflict_do_update(
                index_elements=["org_id", "external_account_key", "date"],
                set_={
                    k: stmt.excluded[k] for k in ("active_users", "key_actions", "seats_used", "departments")
                },
            )
        )
