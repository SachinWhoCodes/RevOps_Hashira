from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from casefile.api.deps import current_org, current_user, get_db
from casefile.cases.levers import what_would_lower_risk
from casefile.cases.relationships import relationship_map
from casefile.cases.service import RecomputeContext, assess
from casefile.db.models import (
    Account,
    AccountCase,
    Activity,
    ActivityType,
    AppUser,
    Assessment,
    CaseStatus,
    ChangeItem,
    ChangeItemStatus,
    ChangeSet,
    ChangeSetStatus,
    Contact,
    Deal,
    DealType,
    MatchMethod,
    Org,
    UsageDaily,
)
from casefile.outcomes.tracker import account_outcomes
from casefile.timeline.snapshot import AccountHistory, Snapshot

router = APIRouter(prefix="/accounts", tags=["accounts"])

RESOLVED = (MatchMethod.unmatched, MatchMethod.ambiguous, MatchMethod.internal_only)


def _parse_as_of(as_of: str | None, org: Org) -> datetime:
    if not as_of:
        return datetime.now(UTC)
    try:
        if len(as_of) == 10:  # a calendar day means "end of that day" in the org's time zone
            return datetime.combine(
                date.fromisoformat(as_of), time(23, 59, 59), tzinfo=ZoneInfo(org.timezone)
            )
        parsed = datetime.fromisoformat(as_of)
    except ValueError as exc:
        raise HTTPException(422, f"bad as_of: {as_of}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@router.get("")
def list_accounts(
    q: str = "", owner: str = "", db: Session = Depends(get_db), org: Org = Depends(current_org)
) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    act = (
        select(
            Activity.account_id.label("aid"),
            func.max(case((Activity.occurred_at <= now, Activity.occurred_at))).label("last_activity_at"),
            func.count()
            .filter(Activity.type == ActivityType.email, Activity.occurred_at > now - timedelta(days=30))
            .label("emails_30d"),
            func.min(
                case(
                    (
                        and_(
                            Activity.type == ActivityType.meeting,
                            Activity.occurred_at > now,
                            Activity.cancelled_at.is_(None),
                        ),
                        Activity.occurred_at,
                    )
                )
            ).label("next_meeting_at"),
        )
        .where(
            Activity.org_id == org.id,
            Activity.account_id.is_not(None),
            Activity.match_method.not_in(RESOLVED),
        )
        .group_by(Activity.account_id)
        .subquery()
    )
    usage = (
        select(UsageDaily.account_id.label("aid"), func.avg(UsageDaily.active_users).label("dau_7d"))
        .where(UsageDaily.org_id == org.id, UsageDaily.date >= (now - timedelta(days=8)).date())
        .group_by(UsageDaily.account_id)
        .subquery()
    )
    renewal = (
        select(
            Deal.account_id.label("aid"),
            func.min(Deal.close_date).label("renewal_close"),
            func.max(Deal.amount).label("renewal_amount"),
        )
        .where(Deal.org_id == org.id, Deal.deal_type == DealType.renewal, Deal.is_closed.is_(False))
        .group_by(Deal.account_id)
        .subquery()
    )
    lost = (
        select(Deal.account_id.label("aid"), func.max(Deal.close_date).label("lost_on"))
        .where(
            Deal.org_id == org.id,
            Deal.deal_type == DealType.renewal,
            Deal.is_closed.is_(True),
            Deal.is_won.is_(False),
        )
        .group_by(Deal.account_id)
        .subquery()
    )
    query = (
        select(
            Account,
            AppUser.name.label("owner_name"),
            act.c.last_activity_at,
            act.c.emails_30d,
            act.c.next_meeting_at,
            usage.c.dau_7d,
            renewal.c.renewal_close,
            renewal.c.renewal_amount,
            lost.c.lost_on,
        )
        .outerjoin(AppUser, AppUser.id == Account.owner_user_id)
        .outerjoin(act, act.c.aid == Account.id)
        .outerjoin(usage, usage.c.aid == Account.id)
        .outerjoin(renewal, renewal.c.aid == Account.id)
        .outerjoin(lost, lost.c.aid == Account.id)
        .where(Account.org_id == org.id)
    )
    if q:
        query = query.where(
            or_(Account.name.ilike(f"%{q}%"), func.array_to_string(Account.domains, ",").ilike(f"%{q}%"))
        )
    if owner:
        query = query.where(Account.owner_user_id == uuid.UUID(owner))
    today = now.astimezone(ZoneInfo(org.timezone)).date()  # "days to renewal" in the company's calendar
    out = []
    for row in db.execute(query.order_by(Account.name)):
        acc: Account = row.Account
        renewal_date = acc.renewal_date or row.renewal_close
        churned = row.renewal_close is None and row.lost_on is not None
        out.append(
            {
                "id": str(acc.id),
                "name": acc.name,
                "domains": acc.domains,
                "owner": row.owner_name,
                "status": "churned" if churned else "active",
                "renewal_date": renewal_date.isoformat() if renewal_date and not churned else None,
                "days_to_renewal": (renewal_date - today).days if renewal_date and not churned else None,
                "arr": float(row.renewal_amount) if row.renewal_amount is not None else None,
                "seats_purchased": acc.seats_purchased,
                "last_activity_at": row.last_activity_at.isoformat() if row.last_activity_at else None,
                "emails_30d": row.emails_30d or 0,
                "next_meeting_at": row.next_meeting_at.isoformat() if row.next_meeting_at else None,
                "active_users_7d": round(float(row.dau_7d), 1) if row.dau_7d is not None else None,
            }
        )
    return out


def _activity_event(a: Any, snap: Snapshot) -> dict[str, Any]:
    names = {c.id: c.name or c.email for c in snap.contacts}
    return {
        "kind": a.type,
        "id": str(a.id),
        "at": a.occurred_at.isoformat(),
        "known_at": a.known_at.isoformat(),
        "scheduled": a.occurred_at > snap.as_of,
        "cancelled": a.cancelled,
        "direction": a.direction,
        "source": a.source,
        "subject": a.subject,
        "snippet": a.snippet,
        "from": a.from_email,
        "participants": list(a.participant_emails),
        "people": [names[c] for c in a.contact_ids if c in names],
        "auto_reply": bool(a.extra.get("auto_reply")),
        "bounce": bool(a.extra.get("bounce")),
        "via": a.extra.get("via"),
        "injection_flag": a.injection_flag,
        "injection_reason": a.injection_reason,
        "injection_quote": a.injection_quote,
        "labels": [
            {
                "label": lab.label,
                "status": lab.status,
                "confidence": round(lab.confidence, 2),
                "quote": lab.quote,
            }
            for lab in a.labels
        ],
        "match": {"method": a.match_method, "confidence": a.match_confidence},
    }


@router.get("/{account_id}")
def account_timeline(
    account_id: uuid.UUID,
    as_of: str | None = None,
    types: list[str] = Query(default=[]),
    limit: int = 300,
    db: Session = Depends(get_db),
    org: Org = Depends(current_org),
) -> dict[str, Any]:
    acc = db.get(Account, account_id)
    if acc is None or acc.org_id != org.id:
        raise HTTPException(404, "account not found")
    t = _parse_as_of(as_of, org)
    history = AccountHistory.load(db, account_id)
    snap = history.as_of(t)
    if snap is None:
        return {"as_of": t.isoformat(), "exists": False, "account": {"id": str(acc.id), "name": acc.name}}
    # Scored live for this date with today's rules: the same code the queue and back-test use.
    ctx = RecomputeContext.build(db, org)
    assessed = assess(
        snap, arr_fallback=ctx.arr_median, ambiguous_records=ctx.ambiguous_by_account.get(str(account_id), 0)
    )
    cases = db.scalars(
        select(AccountCase).where(AccountCase.account_id == account_id).order_by(AccountCase.opened_at.desc())
    ).all()
    # The open case's current plan, so "what would lower this risk?" can point at it and say which of
    # its lines were already written to the tools.
    plan_play, plan_done = None, frozenset[str]()
    open_cases = [c.id for c in cases if c.status == CaseStatus.open]
    if not as_of and open_cases:
        plan = db.scalar(
            select(ChangeSet)
            .where(ChangeSet.case_id.in_(open_cases), ChangeSet.status != ChangeSetStatus.superseded)
            .order_by(ChangeSet.created_at.desc())
            .limit(1)
        )
        if plan is not None:
            plan_play = plan.playbook_id
            plan_done = frozenset(
                db.scalars(
                    select(ChangeItem.action_type).where(
                        ChangeItem.change_set_id == plan.id, ChangeItem.status == ChangeItemStatus.succeeded
                    )
                )
            )
    # The stored, checked explanation applies only to the current view and only if it was written
    # for exactly these signals (same signature); the time machine shows signals, not prose.
    explanation = None
    if not as_of:
        stored = db.scalar(
            select(Assessment)
            .where(Assessment.account_id == account_id)
            .order_by(Assessment.created_at.desc())
            .limit(1)
        )
        if stored is not None and stored.signature == assessed.score.signature and stored.claims is not None:
            report = stored.checker_report or {}
            explanation = {
                "assessment_id": str(stored.id),
                "status": stored.status.value,
                "headline": stored.headline,
                "claims": stored.claims,
                "blind_spot_note": report.get("blind_spot_note") or "",
                "model": stored.model,
                "prompt_version": stored.prompt_version,
                "removed_by_checker": len(report.get("removed", [])),
                "stale_removed": len(report.get("stale_removed", [])),
                "attempts": len(report.get("attempts", [])),
            }
    activities = [a for a in snap.activities if not types or a.type in types]
    events = [_activity_event(a, snap) for a in activities]
    if not types or "crm_change" in types:
        events += [
            {"kind": "crm_change", "at": c["at"].isoformat(), "changes": c["changes"]}
            for c in history.field_changes(t)
            if c["at"] >= t - timedelta(days=400)
        ]
    events.sort(key=lambda e: e["at"], reverse=True)
    weekly: dict[date, list[Any]] = defaultdict(list)
    for u in snap.usage:
        weekly[u.date - timedelta(days=u.date.weekday())].append(u)
    owner = db.get(AppUser, snap.account.owner_user_id) if snap.account.owner_user_id else None
    return {
        "as_of": t.isoformat(),
        "exists": True,
        "account": {
            "id": str(snap.account.id),
            "name": snap.account.name,
            "domains": list(snap.account.domains),
            "owner": owner.name if owner else None,
            "renewal_date": snap.account.renewal_date.isoformat() if snap.account.renewal_date else None,
            "days_to_renewal": snap.days_to_renewal(),
            "arr": float(snap.account.arr) if snap.account.arr is not None else None,
            "seats_purchased": snap.account.seats_purchased,
        },
        "sources": sorted(snap.sources),
        "relationships": relationship_map(snap),
        "contacts": [
            {
                "id": str(c.id),
                "name": c.name,
                "email": c.email,
                "title": c.title,
                "role": c.role,
                "status": c.status,
                "origin": c.origin,
            }
            for c in sorted(snap.contacts, key=lambda c: (c.origin != "crm", c.role, c.name))
        ],
        "deals": [
            {
                "id": str(d.id),
                "name": d.name,
                "type": d.deal_type,
                "stage": d.stage,
                "closed": d.is_closed,
                "won": d.is_won,
                "amount": float(d.amount) if d.amount is not None else None,
                "close_date": d.close_date.isoformat() if d.close_date else None,
                "next_step": d.next_step,
                "next_step_since": d.next_step_since.isoformat() if d.next_step_since else None,
            }
            for d in snap.deals
        ],
        "usage_weekly": [
            {
                "week": week.isoformat(),
                "active_users_avg": round(sum(u.active_users for u in days) / len(days), 1),
                "key_actions": sum(u.key_actions for u in days),
                "seats_used": max((u.seats_used or 0) for u in days),
            }
            for week, days in sorted(weekly.items())
        ],
        "events": events[:limit],
        "event_count": len(events),
        "assessment": {
            "computed_for": t.isoformat(),
            "churned": snap.is_churned(),
            "risk": assessed.score.risk,
            "opportunity": assessed.score.opportunity,
            "confidence": assessed.score.confidence,
            "priority": assessed.score.priority,
            **{
                k: assessed.score.breakdown[k]
                for k in ("risk", "opportunity", "urgency", "arr", "confidence")
            },
            "signals": [h.to_dict() for h in assessed.hits],
            "blind_spots": assessed.blind_spots,
            "levers": what_would_lower_risk(snap, assessed.hits, plan_play=plan_play, plan_done=plan_done),
            "versions": {"scoring": assessed.score.breakdown["scoring_version"]},
        },
        "explanation": explanation,
        # What happened after executed plays (live view only; the time machine shows no plans).
        "outcomes": [] if as_of else account_outcomes(db, account_id),
        "flagged_emails": [
            {
                "id": str(a.id),
                "at": a.occurred_at.isoformat(),
                "subject": a.subject,
                "reason": a.injection_reason,
                "quote": a.injection_quote,
            }
            for a in snap.activities
            if a.injection_flag
        ],
        "cases": [
            {
                "id": str(c.id),
                "kind": c.kind.value,
                "status": c.status.value,
                "opened_at": c.opened_at.isoformat(),
                "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
                "feedback": c.feedback,
                "feedback_note": c.feedback_note,
            }
            for c in cases
        ],
    }


@router.get("/{account_id}/contacts")
def account_contacts(
    account_id: uuid.UUID, db: Session = Depends(get_db), org: Org = Depends(current_org)
) -> list[dict[str, Any]]:
    rows = db.scalars(select(Contact).where(Contact.org_id == org.id, Contact.account_id == account_id))
    return [
        {"id": str(c.id), "email": c.email, "name": c.name, "role": c.role.value, "origin": c.origin}
        for c in rows
    ]


class AskIn(BaseModel):
    question: str


@router.post("/{account_id}/ask")
def ask_about_account(
    account_id: uuid.UUID,
    body: AskIn,
    db: Session = Depends(get_db),
    user: AppUser = Depends(current_user),
) -> dict[str, Any]:
    """Ask Claude about this account. Read-only tools over our data; every sentence is checked
    against the records it cites before it is returned (Phase 6.4)."""
    from casefile.ai.gateway import AiGateway
    from casefile.ai.qa import ask

    acc = db.get(Account, account_id)
    if acc is None or acc.org_id != user.org_id:
        raise HTTPException(404, "account not found")
    org = db.get(Org, user.org_id)
    assert org is not None
    snap = AccountHistory.load(db, account_id).as_of(datetime.now(UTC))
    if snap is None:
        raise HTTPException(404, "account has no data yet")
    ctx = RecomputeContext.build(db, org)
    assessed = assess(
        snap, arr_fallback=ctx.arr_median, ambiguous_records=ctx.ambiguous_by_account.get(str(account_id), 0)
    )
    result = ask(db, AiGateway(db, org.id), snap, assessed, body.question)
    db.commit()
    return result.to_dict()
