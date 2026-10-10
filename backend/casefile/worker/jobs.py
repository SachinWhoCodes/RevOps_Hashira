"""Postgres-backed job queue (no Redis). Consumers claim work with FOR UPDATE SKIP LOCKED."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from casefile.db.models import Job, JobStatus

QUEUED_DEDUPE = text("status = 'queued' AND dedupe_key IS NOT NULL")


def enqueue(
    session: Session,
    kind: str,
    payload: dict[str, Any],
    *,
    org_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
) -> None:
    """Add a job. A job with the same (kind, dedupe_key) already waiting is not duplicated."""
    session.execute(
        insert(Job)
        .values(org_id=org_id, kind=kind, payload=payload, dedupe_key=dedupe_key)
        .on_conflict_do_nothing(index_elements=["kind", "dedupe_key"], index_where=QUEUED_DEDUPE)
    )


def enqueue_recompute(session: Session, org_id: uuid.UUID, account_ids: Iterable[uuid.UUID]) -> int:
    """Mark accounts 'dirty' after new data; Phase 2 recomputes their signals and scores."""
    count = 0
    for account_id in account_ids:
        enqueue(
            session,
            "recompute_account",
            {"account_id": str(account_id)},
            org_id=org_id,
            dedupe_key=f"recompute:{account_id}",
        )
        count += 1
    return count


def claim(session: Session, kinds: list[str], worker_id: str, limit: int = 10) -> list[Job]:
    now = datetime.now(UTC)
    jobs = list(
        session.scalars(
            select(Job)
            .where(Job.status == JobStatus.queued, Job.kind.in_(kinds), Job.run_after <= now)
            .order_by(Job.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for job in jobs:
        job.status, job.locked_at, job.locked_by = JobStatus.running, now, worker_id
        job.attempts += 1
    session.flush()
    return jobs
