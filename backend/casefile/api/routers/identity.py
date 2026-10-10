"""Blind spots for identity: records we could not tie to a customer, and manual mapping."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from casefile.api.deps import current_org, get_db, require_admin
from casefile.db.models import Account, Activity, AppUser, IdentityOverride, MatchMethod, Org, UsageDaily
from casefile.identity.resolver import resolve_identities
from casefile.ingest.util import email_domain
from casefile.worker.jobs import enqueue_recompute

router = APIRouter(prefix="/identity", tags=["identity"])


@router.get("/unmatched")
def unmatched(
    limit: int = 200, db: Session = Depends(get_db), org: Org = Depends(current_org)
) -> dict[str, Any]:
    names = {
        str(i): n for i, n in db.execute(select(Account.id, Account.name).where(Account.org_id == org.id))
    }
    internal = {d.lower() for d in org.internal_domains}
    rows = db.scalars(
        select(Activity)
        .where(
            Activity.org_id == org.id,
            Activity.match_method.in_([MatchMethod.unmatched, MatchMethod.ambiguous]),
        )
        .order_by(Activity.occurred_at.desc())
        .limit(limit)
    ).all()
    total = db.scalar(
        select(func.count()).where(
            Activity.org_id == org.id,
            Activity.match_method.in_([MatchMethod.unmatched, MatchMethod.ambiguous]),
        )
    )
    usage = db.execute(
        select(UsageDaily.external_account_key, func.count(), func.max(UsageDaily.date))
        .where(UsageDaily.org_id == org.id, UsageDaily.account_id.is_(None))
        .group_by(UsageDaily.external_account_key)
        .order_by(UsageDaily.external_account_key)
    ).all()
    return {
        "activities_total": total,
        "activities": [
            {
                "id": str(a.id),
                "type": a.type.value,
                "at": a.occurred_at.isoformat(),
                "subject": a.subject,
                "from": a.from_email,
                "participants": a.participant_emails,
                "external": [e for e in a.participant_emails if email_domain(e) not in internal],
                "method": a.match_method.value,
                "candidates": [{"id": c, "name": names.get(c, c)} for c in a.match_candidates],
            }
            for a in rows
        ],
        "usage_keys": [{"key": k, "rows": n, "last_date": d.isoformat()} for k, n, d in usage],
    }


class OverrideIn(BaseModel):
    # "activity" maps one record only (e.g. a reseller writing about several customers).
    kind: Literal["activity", "email", "domain", "usage_key"]
    value: str
    account_id: uuid.UUID


@router.post("/overrides")
def add_override(
    body: OverrideIn, db: Session = Depends(get_db), user: AppUser = Depends(require_admin)
) -> dict[str, Any]:
    acc = db.get(Account, body.account_id)
    if acc is None or acc.org_id != user.org_id:
        raise HTTPException(404, "account not found")
    stmt = insert(IdentityOverride).values(
        org_id=user.org_id,
        kind=body.kind,
        value=body.value.strip().lower(),
        account_id=acc.id,
        created_by_user_id=user.id,
    )
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["org_id", "kind", "value"],
            set_={
                "account_id": stmt.excluded.account_id,
                "created_by_user_id": stmt.excluded.created_by_user_id,
            },
        )
    )
    org = db.get(Org, user.org_id)
    assert org is not None
    result = resolve_identities(db, org, full=True)
    enqueue_recompute(db, org.id, result.touched_accounts)
    db.commit()
    return {"activities_changed": result.activities_changed, "usage_keys_matched": result.usage_keys_matched}
