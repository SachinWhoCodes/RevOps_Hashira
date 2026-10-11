"""The executor: performs approved change items, exactly once, with a record of every step.

For each ``queued`` item (claimed with ``FOR UPDATE SKIP LOCKED``), in this order:

1. an approval exists and is the latest decision;
2. the hash of (action, target, expected prior value, final payload) equals the approved hash;
3. no earlier attempt succeeded (and an unfinished earlier attempt is reconciled first);
4. for field updates, the live value still equals the ``before`` the person saw — else ``conflict``;
5. shadow mode stops here with ``would_execute``.

Then: audit "execution_started" and commit (if the audit write fails, nothing is written) → call
the writer → audit "succeeded" or "failed" and commit. A crash between the call and the final
commit leaves the item ``executing``; ``reconcile`` searches the target tool for the item's marker
and either records the success or re-queues the item — it never writes twice.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.actions.approvals import latest_approval, rollup
from casefile.actions.spec import SPECS, approval_hash, marker
from casefile.audit import log as audit_log
from casefile.db.models import ChangeItem, ChangeItemStatus, ChangeSet, Decision, ExecutionAttempt
from casefile.executor.writers.base import (
    CannotVerify,
    ItemView,
    Writer,
    WriteRejected,
    WriteResult,
    WriteUnknown,
)

log = structlog.get_logger()
S = ChangeItemStatus


@dataclass
class Outcome:
    item_id: str
    status: str
    detail: dict[str, Any] = field(default_factory=dict)


def view(item: ChangeItem) -> ItemView:
    return ItemView(
        id=item.id,
        action_type=item.action_type,
        target_ref=dict(item.target_ref or {}),
        before=dict(item.before or {}),
        payload=dict(item.final_payload or {}),
        marker=marker(item.id),
    )


class Executor:
    def __init__(
        self,
        session: Session,
        writers: Mapping[str, Writer],
        *,
        shadow: bool = False,
        worker_id: str | None = None,
        after_write: Callable[[ChangeItem, WriteResult], None] | None = None,
    ) -> None:
        self.session = session
        self.writers = writers
        self.shadow = shadow
        self.worker_id = worker_id or f"executor:{socket.gethostname()}:{os.getpid()}"
        # Test hook: runs after the external call and before the final commit (crash simulation).
        self._after_write = after_write

    # ---------------------------------------------------------------- helpers

    def _audit(self, item: ChangeItem, action: str, **details: Any) -> None:
        approval_id = details.pop("approval_id", None)
        audit_log.append(
            self.session,
            item.org_id,
            action,
            actor_type="system",
            actor_id=self.worker_id,
            entity_type="change_item",
            entity_id=str(item.id),
            evidence_ids=list(item.evidence_ids or []),
            approval_id=approval_id,
            details={"seq": item.seq, "type": item.action_type, "system": item.target_system, **details},
        )

    def _finish(self, item: ChangeItem, status: ChangeItemStatus, result: dict[str, Any]) -> None:
        item.status = status
        item.external_result = result
        cs = self.session.get(ChangeSet, item.change_set_id)
        assert cs is not None
        self.session.flush()
        rollup(self.session, cs)

    def _attempts(self, item: ChangeItem) -> list[ExecutionAttempt]:
        return list(
            self.session.scalars(
                select(ExecutionAttempt)
                .where(ExecutionAttempt.change_item_id == item.id)
                .order_by(ExecutionAttempt.attempt_no)
            )
        )

    def _refuse(self, item: ChangeItem, reason: str, **details: Any) -> Outcome:
        self._finish(item, S.failed, {"refused": reason, "retryable": False})
        self._audit(item, "execution_refused", reason=reason, **details)
        self.session.commit()
        log.warning("execution_refused", item=str(item.id), reason=reason)
        return Outcome(str(item.id), "refused", {"reason": reason})

    def _succeed(
        self,
        item: ChangeItem,
        attempt: ExecutionAttempt | None,
        result: WriteResult,
        action: str,
        **extra: Any,
    ) -> Outcome:
        if attempt is not None:
            attempt.status, attempt.finished_at = "succeeded", datetime.now(UTC)
            attempt.external_id = result.external_id
        self._finish(item, S.succeeded, {"id": result.external_id, **result.details})
        self._audit(item, action, external_id=result.external_id, **result.details, **extra)
        self.session.commit()
        return Outcome(str(item.id), "succeeded", {"external_id": result.external_id, **extra})

    # ---------------------------------------------------------------- main loop

    def run_once(self, limit: int = 20) -> list[Outcome]:
        ids = list(
            self.session.scalars(
                select(ChangeItem.id)
                .where(ChangeItem.status == S.queued)
                .order_by(ChangeItem.created_at, ChangeItem.seq)
                .limit(limit)
            )
        )
        self.session.commit()
        outcomes = []
        for item_id in ids:
            outcome = self.process(item_id)
            if outcome is not None:
                outcomes.append(outcome)
        return outcomes

    def process(self, item_id: Any) -> Outcome | None:
        item = self.session.scalar(
            select(ChangeItem)
            .where(ChangeItem.id == item_id, ChangeItem.status == S.queued)
            .with_for_update(skip_locked=True)
        )
        if item is None:
            self.session.rollback()
            return None  # another executor has it, or it is no longer queued
        approval = latest_approval(self.session, item.id)
        if approval is None or approval.decision != Decision.approve:
            return self._refuse(item, "no approval for this line")
        expected = approval_hash(
            item.action_type, item.target_system, item.target_ref, item.before, item.final_payload or {}
        )
        if (
            item.final_payload is None
            or expected != approval.approved_payload_hash
            or expected != item.payload_hash
        ):
            return self._refuse(
                item, "the change no longer matches what was approved", approval_id=approval.id
            )
        attempts = self._attempts(item)
        if any(a.status == "succeeded" for a in attempts):
            self._finish(item, S.succeeded, dict(item.external_result or {}))
            self._audit(item, "execution_duplicate_suppressed", approval_id=approval.id)
            self.session.commit()
            return Outcome(str(item.id), "duplicate_suppressed")
        writer = self.writers.get(item.target_system)
        if writer is None:
            self._finish(
                item, S.failed, {"error": f"no writer configured for {item.target_system}", "retryable": True}
            )
            self._audit(item, "execution_failed", error="no writer configured", approval_id=approval.id)
            self.session.commit()
            return Outcome(str(item.id), "failed", {"error": "no writer configured"})
        v = view(item)
        try:
            if any(a.status in ("started", "unknown") for a in attempts):
                # An earlier run may have written before it crashed: look before writing again.
                found = writer.find(v)
                if found is not None:
                    return self._succeed(item, None, found, "execution_reconciled", found=True)
            if SPECS[item.action_type].field_update:
                prop = item.target_ref.get("property", "hs_next_step")
                live = writer.current(v).get(prop)
                if (live or None) != (item.before.get(prop) or None):
                    self._finish(
                        item, S.conflict, {"conflict": {"expected": item.before.get(prop), "found": live}}
                    )
                    self._audit(
                        item,
                        "execution_conflict",
                        expected=item.before.get(prop),
                        found=live,
                        approval_id=approval.id,
                    )
                    self.session.commit()
                    return Outcome(
                        str(item.id), "conflict", {"expected": item.before.get(prop), "found": live}
                    )
        except WriteRejected as exc:
            if exc.retryable:
                self.session.rollback()
                return Outcome(str(item_id), "deferred", {"error": str(exc)})
            self._finish(item, S.failed, {"error": f"pre-check failed: {exc}", "retryable": True})
            self._audit(item, "execution_failed", error=f"pre-check failed: {exc}", approval_id=approval.id)
            self.session.commit()
            return Outcome(str(item.id), "failed", {"error": str(exc)})
        except (WriteUnknown, CannotVerify) as exc:
            self.session.rollback()
            log.warning("precheck_deferred", item=str(item_id), error=str(exc))
            return Outcome(str(item_id), "deferred", {"error": str(exc)})
        if self.shadow:
            self._finish(item, S.would_execute, {"shadow": True, "payload_hash": expected})
            self._audit(item, "execution_shadowed", payload_hash=expected, approval_id=approval.id)
            self.session.commit()
            return Outcome(str(item.id), "would_execute")

        attempt = ExecutionAttempt(
            org_id=item.org_id, change_item_id=item.id, attempt_no=len(attempts) + 1, status="started"
        )
        self.session.add(attempt)
        item.status = S.executing
        try:
            self._audit(
                item,
                "execution_started",
                payload_hash=expected,
                attempt=attempt.attempt_no,
                approval_id=approval.id,
            )
            self.session.commit()
        except Exception as exc:  # no audit record, no write
            self.session.rollback()
            log.error("audit_failed_no_write", item=str(item_id), error=str(exc))
            return Outcome(str(item_id), "audit_failed", {"error": str(exc)})

        try:
            result = writer.write(v)
        except WriteRejected as exc:
            attempt.status, attempt.finished_at, attempt.error = "failed", datetime.now(UTC), str(exc)
            self._finish(item, S.failed, {"error": str(exc), "retryable": exc.retryable})
            self._audit(
                item, "execution_failed", error=str(exc), retryable=exc.retryable, approval_id=approval.id
            )
            self.session.commit()
            return Outcome(str(item.id), "failed", {"error": str(exc)})
        except WriteUnknown as exc:
            attempt.status, attempt.error = "unknown", str(exc)
            self.session.commit()
            return self._resolve_unknown(item, attempt, writer, v, str(exc))
        if self._after_write is not None:
            self._after_write(item, result)
        return self._succeed(item, attempt, result, "execution_succeeded", approval_id=approval.id)

    def _resolve_unknown(
        self, item: ChangeItem, attempt: ExecutionAttempt, writer: Writer, v: ItemView, error: str
    ) -> Outcome:
        try:
            found = writer.find(v)
        except CannotVerify as exc:
            attempt.finished_at = datetime.now(UTC)
            self._finish(item, S.failed, {"error": f"outcome unknown: {error}; {exc}", "retryable": False})
            self._audit(item, "execution_outcome_unknown", error=error, verify=str(exc))
            self.session.commit()
            return Outcome(str(item.id), "unknown", {"error": error})
        except (WriteRejected, WriteUnknown) as exc:
            log.warning("reconcile_deferred", item=str(item.id), error=str(exc))
            return Outcome(str(item.id), "executing", {"error": error})  # reconcile will look again
        if found is not None:
            return self._succeed(item, attempt, found, "execution_reconciled", found=True)
        attempt.status, attempt.finished_at, attempt.error = "failed", datetime.now(UTC), error
        self._finish(
            item, S.failed, {"error": f"{error} (not found by marker; safe to retry)", "retryable": True}
        )
        self._audit(item, "execution_failed", error=error, checked_marker=True)
        self.session.commit()
        return Outcome(str(item.id), "failed", {"error": error})

    # ---------------------------------------------------------------- crash recovery

    def reconcile(self, older_than: timedelta = timedelta(seconds=120)) -> list[Outcome]:
        """Items left ``executing`` by a crash: find the marker, then record success or re-queue."""
        cutoff = datetime.now(UTC) - older_than
        stuck = list(
            self.session.scalars(
                select(ChangeItem)
                .where(ChangeItem.status == S.executing, ChangeItem.updated_at <= cutoff)
                .with_for_update(skip_locked=True)
            )
        )
        outcomes = []
        for item in stuck:
            writer = self.writers.get(item.target_system)
            if writer is None:
                continue
            attempts = self._attempts(item)
            attempt = attempts[-1] if attempts else None
            try:
                found = writer.find(view(item))
            except CannotVerify as exc:
                self._finish(
                    item, S.failed, {"error": f"outcome unknown after a crash; {exc}", "retryable": False}
                )
                self._audit(item, "execution_outcome_unknown", verify=str(exc))
                self.session.commit()
                outcomes.append(Outcome(str(item.id), "unknown"))
                continue
            except (WriteRejected, WriteUnknown) as exc:
                log.warning("reconcile_deferred", item=str(item.id), error=str(exc))
                continue
            if found is not None:
                outcomes.append(self._succeed(item, attempt, found, "execution_reconciled", found=True))
                continue
            if attempt is not None and attempt.status in ("started", "unknown"):
                attempt.status, attempt.finished_at = "abandoned", datetime.now(UTC)
            item.status = S.queued
            self._audit(item, "execution_reconciled", found=False, requeued=True)
            self.session.commit()
            outcomes.append(Outcome(str(item.id), "requeued"))
        self.session.commit()
        return outcomes


def attempt_count(session: Session, item_id: Any) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(ExecutionAttempt)
            .where(ExecutionAttempt.change_item_id == item_id)
        )
        or 0
    )
