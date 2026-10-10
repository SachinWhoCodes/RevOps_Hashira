"""Append-only, hash-chained audit log (plan rule R5).

Each row's ``hash`` is SHA-256 over the previous row's hash plus this row's canonical content, so
editing or deleting any row breaks every later hash. The database trigger already refuses UPDATE
and DELETE; the chain makes tampering detectable even by someone who can bypass the trigger.
Appends for one organisation are serialised with a transaction-level advisory lock.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from casefile.db.models import AuditLog
from casefile.ingest.util import canonical_json

GENESIS = "0" * 64


def _content(row: dict[str, Any]) -> str:
    keys = (
        "org_id",
        "ts",
        "actor_type",
        "actor_id",
        "action",
        "entity_type",
        "entity_id",
        "evidence_ids",
        "approval_id",
        "details",
        "model",
        "prompt_version",
        "prev_hash",
    )
    return canonical_json({k: row.get(k) for k in keys})


def _hash(row: dict[str, Any]) -> str:
    return hashlib.sha256(_content(row).encode()).hexdigest()


def append(
    session: Session,
    org_id: uuid.UUID,
    action: str,
    *,
    actor_type: str = "system",
    actor_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    evidence_ids: list[str] | None = None,
    approval_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
) -> AuditLog:
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"audit:{org_id}"})
    prev = session.scalar(
        select(AuditLog.hash).where(AuditLog.org_id == org_id).order_by(AuditLog.seq.desc()).limit(1)
    )
    row: dict[str, Any] = {
        "org_id": str(org_id),
        "ts": datetime.now(UTC).isoformat(),
        "actor_type": actor_type,
        "actor_id": actor_id,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "evidence_ids": evidence_ids or [],
        "approval_id": str(approval_id) if approval_id else None,
        "details": details or {},
        "model": model,
        "prompt_version": prompt_version,
        "prev_hash": prev or GENESIS,
    }
    entry = AuditLog(
        org_id=org_id,
        ts=datetime.fromisoformat(row["ts"]),
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        evidence_ids=row["evidence_ids"],
        approval_id=approval_id,
        details=row["details"],
        model=model,
        prompt_version=prompt_version,
        prev_hash=row["prev_hash"],
        hash=_hash(row),
    )
    session.add(entry)
    session.flush()
    return entry


def verify_chain(session: Session, org_id: uuid.UUID) -> dict[str, Any]:
    """Recompute every hash in order. Returns the first broken sequence number, if any."""
    prev = GENESIS
    checked = 0
    for entry in session.scalars(select(AuditLog).where(AuditLog.org_id == org_id).order_by(AuditLog.seq)):
        row: dict[str, Any] = {
            "org_id": str(entry.org_id),
            "ts": entry.ts.astimezone(UTC).isoformat(),
            "actor_type": entry.actor_type,
            "actor_id": entry.actor_id,
            "action": entry.action,
            "entity_type": entry.entity_type,
            "entity_id": entry.entity_id,
            "evidence_ids": list(entry.evidence_ids or []),
            "approval_id": str(entry.approval_id) if entry.approval_id else None,
            "details": entry.details or {},
            "model": entry.model,
            "prompt_version": entry.prompt_version,
            "prev_hash": entry.prev_hash,
        }
        if entry.prev_hash != prev or _hash(row) != entry.hash:
            return {"ok": False, "checked": checked, "broken_at_seq": entry.seq}
        prev = entry.hash
        checked += 1
    total = session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.org_id == org_id))
    return {"ok": True, "checked": checked, "total": total}
