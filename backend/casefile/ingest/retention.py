"""Email retention: full text is deleted after its purge date; snippets stay as evidence.

The data-use request promises this (``docs/runbooks/data-access-request.md`` §1: "Full text is
deleted after 90 days; short snippets are kept for evidence"). Full text lives in two places and both
are cleared:

- the activity row: ``body_text`` and any hidden HTML text kept for the injection banner;
- the raw payload archive: Gmail MIME body data, HubSpot ``hs_email_text`` / ``hs_email_html``.

Kept: the ≤500-character snippet, subject, addresses, dates, AI labels with their short verbatim
quotes, and each raw record's payload hash — so re-fetching the same message is still recognised and
not stored again. Every run is recorded in the audit log (counts only, never content).
"""

from __future__ import annotations

import copy
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from casefile.audit.log import append as audit
from casefile.db.models import Activity, ActivityType, Org, RawRecord
from casefile.ingest.util import chunks

HUBSPOT_BODY_PROPS = ("hs_email_text", "hs_email_html")


def strip_gmail(payload: dict[str, Any]) -> dict[str, Any]:
    """A Gmail message with every MIME body removed (headers, labels and snippet stay)."""
    out = copy.deepcopy(payload)

    def walk(part: dict[str, Any]) -> None:
        body = part.get("body")
        if isinstance(body, dict) and "data" in body:
            part["body"] = {"size": body.get("size", 0), "purged": True}
        for child in part.get("parts") or []:
            walk(child)

    if isinstance(out.get("payload"), dict):
        walk(out["payload"])
    out["_purged"] = True
    return out


def strip_hubspot_email(payload: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(payload)
    props = out.get("properties") or {}
    for key in HUBSPOT_BODY_PROPS:
        props.pop(key, None)
    out["_purged"] = True
    return out


def purge_expired_bodies(session: Session, org: Org, *, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    due = session.execute(
        select(Activity.id, Activity.source, Activity.source_id, Activity.extra).where(
            Activity.org_id == org.id,
            Activity.type == ActivityType.email,
            Activity.body_purge_after.is_not(None),
            Activity.body_purge_after <= now,
            or_(Activity.body_text.is_not(None), Activity.extra.has_key("hidden_text")),
        )
    ).all()
    raw_ids: dict[str, set[str]] = defaultdict(set)
    ids: list[uuid.UUID] = []
    for row in due:
        ids.append(row.id)
        if row.source == "gmail" and (row.extra or {}).get("gmail_id"):
            raw_ids["gmail.message"].add(str(row.extra["gmail_id"]))
        elif row.source == "hubspot" and str(row.source_id).startswith("email:"):
            raw_ids["hubspot.email"].add(str(row.source_id).split(":", 1)[1])
    for batch in chunks(ids, 1000):
        session.execute(
            update(Activity)
            .where(Activity.id.in_(batch))
            .values(body_text=None, extra=Activity.extra.op("-")("hidden_text"))
            .execution_options(synchronize_session=False)
        )
    raw_purged = 0
    strippers = {"gmail.message": strip_gmail, "hubspot.email": strip_hubspot_email}
    for object_type, external_ids in raw_ids.items():
        for ext_batch in chunks(sorted(external_ids), 500):
            for raw in session.scalars(
                select(RawRecord).where(
                    RawRecord.org_id == org.id,
                    RawRecord.object_type == object_type,
                    RawRecord.external_id.in_(ext_batch),
                )
            ):
                if not (raw.payload or {}).get("_purged"):
                    raw.payload = strippers[object_type](raw.payload)  # new dict: JSONB change is saved
                    raw_purged += 1
    result = {"emails": len(ids), "raw_records": raw_purged}
    if ids or raw_purged:
        audit(session, org.id, "retention_purge", details={**result, "purged_before": now.isoformat()})
    return result
