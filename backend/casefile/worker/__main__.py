"""Background worker: scheduled syncs + queued jobs. Run with ``python -m casefile.worker``.

One process, two loops (APScheduler):
- every minute, sync each active read connection whose interval has elapsed;
- every few seconds, run queued ``sync_connection`` jobs (the UI's "Sync now").
``recompute_account`` jobs are produced here and consumed from Phase 2 on.
"""

from __future__ import annotations

import os
import socket
from datetime import UTC, datetime, timedelta

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import select

from casefile.db.models import Connection, ConnectionStatus, JobStatus, Zone
from casefile.db.session import get_sessionmaker
from casefile.ingest.sync_runner import run_sync
from casefile.worker.jobs import claim

log = structlog.get_logger()
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def due_connections() -> None:
    session = get_sessionmaker()()
    try:
        now = datetime.now(UTC)
        conns = session.scalars(
            select(Connection).where(
                Connection.zone == Zone.read, Connection.status == ConnectionStatus.active
            )
        ).all()
        for conn in conns:
            last = conn.last_sync_started_at
            if last is None or now - last >= timedelta(minutes=conn.interval_minutes):
                run_sync(session, conn.id)
    finally:
        session.close()


def queued_jobs() -> None:
    session = get_sessionmaker()()
    try:
        jobs = claim(session, ["sync_connection"], WORKER_ID, limit=5)
        session.commit()
        for job in jobs:
            try:
                run = run_sync(session, job.payload["connection_id"])
                job.status = JobStatus.done if run.status.value == "succeeded" else JobStatus.failed
                job.last_error = run.error
            except Exception as exc:
                session.rollback()
                job.status, job.last_error = JobStatus.failed, str(exc)[:2000]
            job.finished_at = datetime.now(UTC)
            session.merge(job)
            session.commit()
    finally:
        session.close()


def main() -> None:
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        due_connections,
        "interval",
        minutes=1,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC),
    )
    scheduler.add_job(queued_jobs, "interval", seconds=5, max_instances=1, coalesce=True)
    log.info("worker_started", worker=WORKER_ID)
    scheduler.start()


if __name__ == "__main__":
    main()
