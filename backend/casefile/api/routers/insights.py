"""Blind-spot rollup, read-only configuration, and the audit log."""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.api.deps import current_org, get_db
from casefile.api.routers.connections import freshness
from casefile.audit.log import verify_chain
from casefile.db.models import (
    Account,
    Activity,
    Assessment,
    AuditLog,
    Connection,
    MatchMethod,
    Org,
    UsageDaily,
)
from casefile.ingest.mapping import org_mapping
from casefile.scoring.points import default_scoring
from casefile.signals.base import ENGINE_VERSION, REGISTRY, default_thresholds

router = APIRouter(tags=["insights"])


def _latest_assessments(db: Session, org: Org) -> dict[Any, Assessment]:
    latest: dict[Any, Assessment] = {}
    for a in db.scalars(
        select(Assessment).where(Assessment.org_id == org.id).order_by(Assessment.created_at)
    ):
        latest[a.account_id] = a
    return latest


@router.get("/blind-spots")
def blind_spots(db: Session = Depends(get_db), org: Org = Depends(current_org)) -> dict[str, Any]:
    names = dict(db.execute(select(Account.id, Account.name).where(Account.org_id == org.id)).all())
    by_code: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "accounts": [], "text": ""})
    latest = _latest_assessments(db, org)
    for account_id, a in latest.items():
        for spot in (a.breakdown or {}).get("blind_spots", []):
            entry = by_code[spot["code"]]
            entry["count"] += 1
            entry["text"] = spot["text"]
            entry["accounts"].append({"id": str(account_id), "name": names.get(account_id, "?")})
    for entry in by_code.values():
        entry["accounts"].sort(key=lambda x: x["name"])
    unresolved = dict(
        db.execute(
            select(Activity.match_method, func.count())
            .where(
                Activity.org_id == org.id,
                Activity.match_method.in_([MatchMethod.unmatched, MatchMethod.ambiguous]),
            )
            .group_by(Activity.match_method)
        ).all()
    )
    usage_keys = db.scalar(
        select(func.count(func.distinct(UsageDaily.external_account_key))).where(
            UsageDaily.org_id == org.id, UsageDaily.account_id.is_(None)
        )
    )
    now = datetime.now(UTC)
    connections: list[dict[str, Any]] = [
        {
            "kind": c.kind.value,
            "name": c.name,
            "status": c.status.value,
            "freshness": freshness(c, now),
            "last_error": c.last_error,
        }
        for c in db.scalars(select(Connection).where(Connection.org_id == org.id))
    ]
    return {
        "accounts_assessed": len(latest),
        "by_code": dict(sorted(by_code.items(), key=lambda kv: -kv[1]["count"])),
        "unmatched_records": unresolved.get(MatchMethod.unmatched, 0),
        "ambiguous_records": unresolved.get(MatchMethod.ambiguous, 0),
        "unmatched_usage_keys": usage_keys or 0,
        "connections": connections,
        "connections_not_fresh": [
            c for c in connections if c["freshness"]["state"] != "fresh" or c["status"] == "error"
        ],
    }


@router.get("/config")
def config(db: Session = Depends(get_db), org: Org = Depends(current_org)) -> dict[str, Any]:
    return {
        "engine_version": ENGINE_VERSION,
        "signals": [
            {"name": s.name, "polarity": s.polarity.value, "source": s.source, "description": s.description}
            for s in REGISTRY.values()
        ],
        "thresholds": default_thresholds(),
        "scoring": default_scoring(),
        "field_mapping": org_mapping(db, org.id),
        "editable": False,
    }


def _audit_row(r: AuditLog) -> dict[str, Any]:
    return {
        "seq": r.seq,
        "ts": r.ts.isoformat(),
        "actor": f"{r.actor_type}:{r.actor_id}" if r.actor_id else r.actor_type,
        "action": r.action,
        "entity_type": r.entity_type,
        "entity_id": r.entity_id,
        "approval_id": str(r.approval_id) if r.approval_id else None,
        "details": r.details,
        "evidence_count": len(r.evidence_ids or []),
        "model": r.model,
        "prompt_version": r.prompt_version,
        "hash": r.hash,
        "prev_hash": r.prev_hash,
    }


@router.get("/audit", response_model=None)
def audit_log(
    limit: int = Query(200, le=5000),
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    format: str = Query("json", pattern="^(json|csv|download)$"),
    db: Session = Depends(get_db),
    org: Org = Depends(current_org),
) -> list[dict[str, Any]] | Response:
    q = select(AuditLog).where(AuditLog.org_id == org.id)
    if action:
        q = q.where(AuditLog.action.startswith(action))
    if entity_type:
        q = q.where(AuditLog.entity_type == entity_type)
    if entity_id:
        q = q.where(AuditLog.entity_id == entity_id)
    if actor_type:
        q = q.where(AuditLog.actor_type == actor_type)
    if since:
        q = q.where(AuditLog.ts >= since)
    if until:
        q = q.where(AuditLog.ts <= until)
    rows = [_audit_row(r) for r in db.scalars(q.order_by(AuditLog.seq.desc()).limit(limit))]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if format == "csv":
        buf = io.StringIO()
        cols = [
            "seq",
            "ts",
            "actor",
            "action",
            "entity_type",
            "entity_id",
            "approval_id",
            "model",
            "prompt_version",
            "evidence_count",
            "hash",
            "prev_hash",
            "details",
        ]
        writer = csv.DictWriter(buf, fieldnames=cols)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {**{c: r.get(c) for c in cols}, "details": json.dumps(r["details"], sort_keys=True)}
            )
        return Response(
            buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="casefile-audit-{stamp}.csv"'},
        )
    if format == "download":
        return Response(
            json.dumps(rows, indent=2, default=str),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="casefile-audit-{stamp}.json"'},
        )
    return rows


@router.get("/audit/verify")
def audit_verify(db: Session = Depends(get_db), org: Org = Depends(current_org)) -> dict[str, Any]:
    return verify_chain(db, org.id)


@router.get("/plays")
def plays(db: Session = Depends(get_db), org: Org = Depends(current_org)) -> dict[str, Any]:
    """Plays that work: how executed change sets turned out at +7/+14/+30 days (measured after)."""
    from casefile.outcomes.tracker import CHECK_DAYS, plays_table

    return {"check_days": list(CHECK_DAYS), "plays": plays_table(db, org)}
