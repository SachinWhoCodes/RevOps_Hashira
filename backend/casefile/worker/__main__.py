"""Background worker: scheduled syncs + queued jobs. Run with ``python -m casefile.worker``.

One process, three loops (APScheduler):
- every minute, sync each active read connection whose interval has elapsed;
- every few seconds, run queued jobs: ``sync_connection`` (the UI's "Sync now"),
  ``recompute_account`` (accounts whose data changed), ``read_emails`` (AI email reader on new
  inbound customer email), ``write_explanation`` (AI case writer for open cases) and
  ``plan_case`` (playbook planner; proposes a change set for approval — it never writes);
- every 6 hours, rescore every account, because "days since" counts move even without new data.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import select

from casefile.actions.planner import plan_case
from casefile.ai.email_reader import pending_emails, read_emails
from casefile.ai.explain import explain_assessment
from casefile.ai.gateway import AiGateway, load_prompt
from casefile.cases.service import RecomputeContext, recompute_account, recompute_all
from casefile.config import get_settings
from casefile.connectors.hubspot.live import HubSpotLive
from casefile.db.models import Connection, ConnectionStatus, Job, JobStatus, Org, Zone
from casefile.db.session import get_sessionmaker
from casefile.ingest.retention import purge_expired_bodies
from casefile.ingest.sync_runner import run_sync
from casefile.outcomes.tracker import track
from casefile.worker.jobs import claim, enqueue, enqueue_recompute

log = structlog.get_logger()
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
READ_EMAILS_PER_JOB = 100  # keeps each job short; the job re-queues itself while emails remain


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


def live_crm() -> HubSpotLive | None:
    """Read-zone HubSpot access for the planner's "before" values (None when not configured)."""
    token = get_settings().read_hubspot_token
    return HubSpotLive(token.get_secret_value()) if token else None


def _finish(session: object, job: Job, ok: bool, error: str | None) -> None:
    job.status = JobStatus.done if ok else JobStatus.failed
    job.last_error, job.finished_at = error, datetime.now(UTC)


def queued_jobs() -> None:
    session = get_sessionmaker()()
    try:
        jobs = claim(
            session,
            ["sync_connection", "recompute_account", "read_emails", "write_explanation", "plan_case"],
            WORKER_ID,
            limit=50,
        )
        session.commit()
        contexts: dict[object, RecomputeContext] = {}
        for job in jobs:
            try:
                if job.kind == "sync_connection":
                    run = run_sync(session, job.payload["connection_id"])
                    _finish(session, job, run.status.value == "succeeded", run.error)
                elif job.kind == "recompute_account":
                    org = session.get(Org, job.org_id)
                    assert org is not None
                    ctx = contexts.setdefault(org.id, RecomputeContext.build(session, org))
                    recompute_account(session, job.payload["account_id"], ctx)
                    _finish(session, job, True, None)
                elif job.kind == "read_emails":
                    org = session.get(Org, job.org_id)
                    assert org is not None
                    _, version = load_prompt("email_reader")
                    emails = pending_emails(session, org, version, limit=READ_EMAILS_PER_JOB)
                    out = read_emails(session, org, AiGateway(session, org.id), emails)
                    enqueue_recompute(session, org.id, out.accounts)
                    if len(emails) == READ_EMAILS_PER_JOB and not out.failed:
                        enqueue(
                            session,
                            "read_emails",
                            {"org_id": str(org.id)},
                            org_id=org.id,
                            dedupe_key=f"read_emails:{org.id}",
                        )
                    _finish(session, job, True, json.dumps(out.failed) if out.failed else None)
                elif job.kind == "plan_case":
                    gateway = AiGateway(session, job.org_id) if job.org_id else None
                    plan_case(session, uuid.UUID(job.payload["case_id"]), gateway=gateway, live=live_crm())
                    _finish(session, job, True, None)
                else:  # write_explanation
                    gateway = AiGateway(session, job.org_id) if job.org_id else None
                    explain_assessment(session, uuid.UUID(job.payload["assessment_id"]), gateway)
                    _finish(session, job, True, None)
            except Exception as exc:
                session.rollback()
                _finish(session, job, False, f"{type(exc).__name__}: {exc}"[:2000])
                log.error("job_failed", kind=job.kind, job=job.id, error=str(exc))
            session.merge(job)
            session.commit()
    finally:
        session.close()


def rescore_all() -> None:
    session = get_sessionmaker()()
    try:
        for org in session.scalars(select(Org)).all():
            totals = recompute_all(session, org)
            log.info("rescored", org=org.slug, **totals)
    finally:
        session.close()


def track_outcomes() -> None:
    """Outcome tracker (6.1): checkpoints at +7/+14/+30 days after each executed play."""
    session = get_sessionmaker()()
    try:
        for org in session.scalars(select(Org)).all():
            counts = track(session, org)
            session.commit()
            if counts["measured"]:
                log.info("outcomes_measured", org=org.slug, **counts)
    finally:
        session.close()


def purge_bodies() -> None:
    """Email retention: full text past its purge date is deleted (casefile.ingest.retention)."""
    session = get_sessionmaker()()
    try:
        for org in session.scalars(select(Org)).all():
            result = purge_expired_bodies(session, org)
            session.commit()
            if result["emails"]:
                log.info("retention_purge", org=org.slug, **result)
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
    scheduler.add_job(
        rescore_all,
        "interval",
        hours=6,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC) + timedelta(minutes=2),
    )
    scheduler.add_job(
        track_outcomes,
        "interval",
        hours=1,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC) + timedelta(minutes=3),
    )
    scheduler.add_job(
        purge_bodies,
        "interval",
        hours=6,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC) + timedelta(minutes=5),
    )
    log.info("worker_started", worker=WORKER_ID)
    scheduler.start()


if __name__ == "__main__":
    main()
