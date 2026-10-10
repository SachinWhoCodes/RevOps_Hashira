from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.api.deps import current_org, get_db, require_admin
from casefile.config import get_settings
from casefile.db.models import AppUser, Connection, Org, RawRecord, SyncRun, Zone
from casefile.worker.jobs import enqueue

router = APIRouter(prefix="/connections", tags=["connections"])


def freshness(conn: Connection, now: datetime) -> dict[str, Any]:
    """'fresh' within N sync intervals of the last success, otherwise 'stale since <last success>'."""
    limit = timedelta(minutes=conn.interval_minutes * get_settings().stale_after_intervals)
    if conn.last_success_at is None:
        state = "never_synced"
    elif now - conn.last_success_at > limit:
        state = "stale"
    else:
        state = "fresh"
    return {
        "state": state,
        "stale_since": conn.last_success_at.isoformat()
        if state == "stale" and conn.last_success_at
        else None,
    }


@router.get("")
def list_connections(db: Session = Depends(get_db), org: Org = Depends(current_org)) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    counts = dict(
        db.execute(
            select(RawRecord.connection_id, func.count())
            .where(RawRecord.org_id == org.id)
            .group_by(RawRecord.connection_id)
        ).all()
    )
    out = []
    for conn in db.scalars(select(Connection).where(Connection.org_id == org.id).order_by(Connection.kind)):
        last_run = db.scalar(
            select(SyncRun).where(SyncRun.connection_id == conn.id).order_by(SyncRun.id.desc()).limit(1)
        )
        out.append(
            {
                "id": str(conn.id),
                "kind": conn.kind.value,
                "name": conn.name,
                "zone": conn.zone.value,
                "status": conn.status.value,
                "interval_minutes": conn.interval_minutes,
                "last_success_at": conn.last_success_at.isoformat() if conn.last_success_at else None,
                "last_error": conn.last_error,
                "last_error_at": conn.last_error_at.isoformat() if conn.last_error_at else None,
                "consecutive_failures": conn.consecutive_failures,
                "raw_records": counts.get(conn.id, 0),
                "freshness": freshness(conn, now),
                "last_run": None
                if last_run is None
                else {
                    "status": last_run.status.value,
                    "started_at": last_run.started_at.isoformat(),
                    "finished_at": last_run.finished_at.isoformat() if last_run.finished_at else None,
                    "fetched": last_run.records_fetched,
                    "new": last_run.records_new,
                    "error": last_run.error,
                },
            }
        )
    return out


@router.post("/{connection_id}/sync")
def request_sync(
    connection_id: str, db: Session = Depends(get_db), user: AppUser = Depends(require_admin)
) -> dict[str, Any]:
    conn = db.scalar(
        select(Connection).where(Connection.id == connection_id, Connection.org_id == user.org_id)
    )
    if conn is None or conn.zone != Zone.read:
        raise HTTPException(404, "connection not found")
    enqueue(
        db,
        "sync_connection",
        {"connection_id": str(conn.id)},
        org_id=conn.org_id,
        dedupe_key=f"sync:{conn.id}",
    )
    db.commit()
    return {"queued": True}
