"""Run one connection's sync: fetch → store raw → normalise new → resolve identities → mark dirty."""

from __future__ import annotations

import traceback
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from casefile.ai.injection import scan_injections
from casefile.connectors.base import ConnectorError, RawItem, Reader
from casefile.db.models import (
    Connection,
    ConnectionKind,
    ConnectionStatus,
    Org,
    RawRecord,
    SyncRun,
    SyncStatus,
)
from casefile.identity.resolver import relink_crm_refs, resolve_identities
from casefile.ingest.context import NormalizeContext
from casefile.ingest.normalize import normalize
from casefile.ingest.util import chunks, payload_hash
from casefile.worker.jobs import enqueue, enqueue_recompute

# Sources whose new records can change renewal patterns (usage, deal history); mail alone does not
# finish a cycle, so it waits for the daily retrain.
PATTERN_SOURCES = {
    ConnectionKind.usage_csv,
    ConnectionKind.usage_sql,
    ConnectionKind.fixture,
    ConnectionKind.hubspot,
}

log = structlog.get_logger()

ReaderFactory = Callable[[Connection, Session], Reader]


def store_raw(session: Session, connection: Connection, items: list[RawItem]) -> list[RawItem]:
    """Store payloads; return only items whose exact payload we had not seen before."""
    hashes = [payload_hash(i.payload) for i in items]
    new_keys: set[tuple[str, str, str]] = set()
    rows = [
        {
            "org_id": connection.org_id,
            "connection_id": connection.id,
            "object_type": item.object_type,
            "external_id": item.external_id,
            "payload": item.payload,
            "payload_hash": h,
        }
        for item, h in zip(items, hashes, strict=True)
    ]
    for batch in chunks(rows, 500):
        result = session.execute(
            insert(RawRecord)
            .values(batch)
            .on_conflict_do_nothing(
                index_elements=["org_id", "connection_id", "object_type", "external_id", "payload_hash"]
            )
            .returning(RawRecord.object_type, RawRecord.external_id, RawRecord.payload_hash)
        )
        new_keys.update((r[0], r[1], r[2]) for r in result)
    return [
        item
        for item, h in zip(items, hashes, strict=True)
        if (item.object_type, item.external_id, h) in new_keys
    ]


def run_sync(
    session: Session, connection_id: uuid.UUID, reader_factory: ReaderFactory | None = None
) -> SyncRun:
    from casefile.connectors.registry import build_reader

    factory = reader_factory or build_reader
    conn = session.get(Connection, connection_id)
    if conn is None:
        raise ValueError(f"connection {connection_id} not found")
    org = session.get(Org, conn.org_id)
    assert org is not None
    run = SyncRun(org_id=org.id, connection_id=conn.id)
    session.add(run)
    conn.last_sync_started_at = datetime.now(UTC)
    session.commit()
    run_id = run.id
    ctx = NormalizeContext(session=session, org=org)
    log.info("sync_started", connection=conn.name, kind=conn.kind.value)
    try:
        reader = factory(conn, session)
        for page in reader.pages(dict(conn.cursor or {})):
            new_items = store_raw(session, conn, page.items) if page.items else []
            unknown = normalize(ctx, new_items)
            if unknown:
                log.warning("unknown_object_types", types=unknown)
            conn.cursor = page.cursor
            run.pages += 1
            run.records_fetched += len(page.items)
            run.records_new += len(new_items)
            session.commit()
        relink_crm_refs(session, org)
        resolved = resolve_identities(session, org)
        flagged = scan_injections(session, org)  # rule-based, no AI: always runs
        dirty = ctx.touched_accounts | resolved.touched_accounts | {uuid.UUID(a) for a in flagged}
        enqueue_recompute(session, org.id, dirty)
        if run.records_new:
            enqueue(
                session,
                "read_emails",
                {"org_id": str(org.id)},
                org_id=org.id,
                dedupe_key=f"read_emails:{org.id}",
            )
        if run.records_new and conn.kind in PATTERN_SOURCES:
            # New usage or CRM history: learn the renewal patterns again (no AI). Waits two minutes so
            # a burst of syncs trains once.
            enqueue(
                session,
                "train_patterns",
                {"org_id": str(org.id)},
                org_id=org.id,
                dedupe_key=f"train_patterns:{org.id}",
                run_after=datetime.now(UTC) + timedelta(minutes=2),
            )
        now = datetime.now(UTC)
        conn.last_success_at, conn.status = now, ConnectionStatus.active
        conn.consecutive_failures, conn.last_error = 0, None
        run.status, run.finished_at = SyncStatus.succeeded, now
        session.commit()
        log.info(
            "sync_finished",
            connection=conn.name,
            fetched=run.records_fetched,
            new=run.records_new,
            matched_changes=resolved.activities_changed,
            dirty_accounts=len(dirty),
        )
    except Exception as exc:  # recorded on the connection; the scheduler keeps running
        session.rollback()
        conn = session.get(Connection, connection_id)
        run_row = session.get(SyncRun, run_id)
        assert conn is not None and run_row is not None
        now = datetime.now(UTC)
        retryable = isinstance(exc, ConnectorError) and exc.retryable
        conn.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        conn.last_error_at = now
        conn.consecutive_failures += 1
        if not retryable:
            conn.status = ConnectionStatus.error
        run_row.status, run_row.finished_at, run_row.error = SyncStatus.failed, now, conn.last_error
        session.commit()
        log.error("sync_failed", connection=conn.name, error=conn.last_error, retryable=retryable)
        if not isinstance(exc, ConnectorError):
            log.debug("sync_traceback", tb=traceback.format_exc())
        return run_row
    return run
