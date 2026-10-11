"""Renewal patterns: the learned library, every client's open cycle compared with finished ones."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.api.deps import current_org, current_user, get_db, require_admin
from casefile.backtest.outcomes import POSITIVE
from casefile.db.models import (
    Account,
    Activity,
    ActivityType,
    AppUser,
    CyclePattern,
    Direction,
    MatchMethod,
    Org,
    PatternMatch,
    PatternModel,
    RenewalOutcome,
    UsageDaily,
    UserRole,
)
from casefile.patterns.features import SPEC
from casefile.patterns.matching import StageModel, project
from casefile.patterns.train import latest_model, train

router = APIRouter(tags=["patterns"])
UNRESOLVED = (MatchMethod.unmatched, MatchMethod.ambiguous, MatchMethod.internal_only)
STRAND_DAYS = 760


def _model_or_404(db: Session, org: Org) -> PatternModel:
    model = latest_model(db, org.id)
    if model is None:
        raise HTTPException(404, "no pattern library yet")
    return model


def _stages(model: PatternModel) -> dict[int, StageModel]:
    return {int(s): StageModel.from_dict(d) for s, d in (model.params.get("stages") or {}).items()}


def _overview(db: Session, model: PatternModel | None, user: AppUser) -> dict[str, Any]:
    if model is None:
        return {"model": None, "can_train": user.role == UserRole.admin}
    stages = []
    for s, m in sorted(_stages(model).items()):
        ranked = sorted(((w, k) for k, w in m.weights.items() if w > 0), reverse=True)
        stages.append(
            {
                "stage": s,
                "n": m.n,
                "lost": m.lost,
                "base_rate": m.base_rate,
                "ready": m.ready,
                "separation": round(ranked[0][0], 3) if ranked else 0.0,
                "top": [{"feature": k, "label": SPEC[k].label, "weight": round(w, 3)} for w, k in ranked[:4]],
                "weights": {k: round(w, 3) for k, w in m.weights.items()},
                "means": m.means,
            }
        )
    verdicts: dict[str, int] = defaultdict(int)
    for v, n in db.execute(
        select(PatternMatch.verdict, func.count())
        .where(PatternMatch.model_id == model.id)
        .group_by(PatternMatch.verdict)
    ):
        verdicts[v or "none"] += int(n)
    stats = model.stats or {}
    params = model.params or {}
    return {
        "model": {
            "id": str(model.id),
            "version": model.version,
            "status": model.status,
            "label": model.label.value,
            "engine_version": model.engine_version,
            "trained_at": model.trained_at.isoformat(),
            "k": params.get("k"),
            "path_steps": params.get("path_steps"),
        },
        "stats": stats,
        "readiness": {
            "cycles": stats.get("described_cycles", 0),
            "lost": stats.get("lost_cycles", 0),
            "min_cycles": params.get("min_cycles"),
            "min_lost": params.get("min_lost"),
        },
        "stages": stages,
        "evaluation": model.evaluation,
        "features": params.get("features", []),
        "excluded_signals": params.get("excluded_signals", []),
        "label_coverage": params.get("label_coverage"),
        "verdicts": dict(verdicts),
        "can_train": user.role == UserRole.admin,
    }


@router.get("/patterns")
def overview(
    db: Session = Depends(get_db), org: Org = Depends(current_org), user: AppUser = Depends(current_user)
) -> dict[str, Any]:
    return _overview(db, latest_model(db, org.id), user)


@router.post("/patterns/train")
def train_now(
    db: Session = Depends(get_db), org: Org = Depends(current_org), user: AppUser = Depends(require_admin)
) -> dict[str, Any]:
    """Learn from every finished renewal and match every client again (about 10 s on 200 accounts)."""
    model = train(db, org)
    db.commit()
    return _overview(db, model, user)


def _owners(db: Session, org: Org) -> dict[uuid.UUID, str]:
    return {u.id: u.name for u in db.scalars(select(AppUser).where(AppUser.org_id == org.id))}


@router.get("/patterns/clients")
def clients(db: Session = Depends(get_db), org: Org = Depends(current_org)) -> list[dict[str, Any]]:
    model = latest_model(db, org.id)
    if model is None:
        return []
    owners = _owners(db, org)
    out = []
    for m, acc in db.execute(
        select(PatternMatch, Account)
        .join(Account, Account.id == PatternMatch.account_id)
        .where(PatternMatch.model_id == model.id)
        .order_by(Account.name)
    ):
        d = m.detail or {}
        now = (d.get("now") or {}).get("f") or {}
        out.append(
            {
                "account_id": str(acc.id),
                "name": acc.name,
                "owner": owners.get(acc.owner_user_id) if acc.owner_user_id else None,
                "arr": float(acc.arr) if acc.arr is not None else None,
                "renewal_date": m.renewal_date.isoformat() if m.renewal_date else None,
                "days_to_renewal": m.days_to_renewal,
                "stage": m.stage,
                "verdict": m.verdict,
                "lost": m.lost,
                "k": m.k,
                "neighbours": [bool(n["lost"]) for n in d.get("neighbours", [])],
                "risk_now": now.get("risk"),
                "note": d.get("note"),
            }
        )
    return out


def _cycle_view(c: CyclePattern, name: str) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "account_id": str(c.account_id),
        "name": name,
        "cycle_start": c.cycle_start.isoformat() if c.cycle_start else None,
        "cycle_end": c.cycle_end.isoformat(),
        "outcome": c.outcome,
        "lost": c.outcome in POSITIVE,
        "arr_before": float(c.arr_before) if c.arr_before is not None else None,
        "arr_after": float(c.arr_after) if c.arr_after is not None else None,
        "points": [
            {"days": int(s), "at": p["at"], "f": p["f"]}
            for s, p in sorted(c.points.items(), key=lambda i: -int(i[0]))
        ],
    }


@router.get("/patterns/clients/{account_id}")
def client(
    account_id: uuid.UUID, db: Session = Depends(get_db), org: Org = Depends(current_org)
) -> dict[str, Any]:
    model = _model_or_404(db, org)
    row = db.execute(
        select(PatternMatch, Account)
        .join(Account, Account.id == PatternMatch.account_id)
        .where(PatternMatch.model_id == model.id, PatternMatch.account_id == account_id)
    ).first()
    if row is None:
        raise HTTPException(404, "client not in the latest pattern library")
    m, acc = row
    d = dict(m.detail or {})
    ids = [int(n["cycle_id"]) for n in d.get("neighbours", [])]
    cycles = {
        str(c.id): _cycle_view(c, name)
        for c, name in db.execute(
            select(CyclePattern, Account.name)
            .join(Account, Account.id == CyclePattern.account_id)
            .where(CyclePattern.id.in_(ids))
        )
    }
    neighbours = [
        {**n, "cycle": cycles[n["cycle_id"]]} for n in d.get("neighbours", []) if n["cycle_id"] in cycles
    ]
    stage = _stages(model).get(m.stage) if m.stage else None
    past = list(
        db.scalars(
            select(RenewalOutcome)
            .where(RenewalOutcome.account_id == acc.id)
            .order_by(RenewalOutcome.renewal_date)
        )
    )
    return {
        "account_id": str(acc.id),
        "name": acc.name,
        "owner": _owners(db, org).get(acc.owner_user_id) if acc.owner_user_id else None,
        "arr": float(acc.arr) if acc.arr is not None else None,
        "renewal_date": m.renewal_date.isoformat() if m.renewal_date else None,
        "days_to_renewal": m.days_to_renewal,
        "stage": m.stage,
        "verdict": m.verdict,
        "computed_at": m.computed_at.isoformat(),
        "cycle_start": d.get("cycle_start"),
        "note": d.get("note"),
        "now": d.get("now"),
        "trajectory": d.get("trajectory", []),
        "mix": d.get("mix"),
        "compared": d.get("compared"),
        "differences": d.get("differences", []),
        "neighbours": neighbours,
        "stage_model": None
        if stage is None
        else {
            "stage": stage.stage,
            "n": stage.n,
            "lost": stage.lost,
            "base_rate": stage.base_rate,
            "ready": stage.ready,
        },
        "past_cycles": [
            {
                "renewal_date": o.renewal_date.isoformat(),
                "outcome": o.outcome.value,
                "lost": o.outcome.value in POSITIVE,
            }
            for o in past
        ],
        "label": model.label.value,
    }


@router.get("/patterns/map")
def pattern_map(stage: int, db: Session = Depends(get_db), org: Org = Depends(current_org)) -> dict[str, Any]:
    """Finished cycles and current clients at one stage, placed by the same notion of "alike" the
    matching uses (principal components of the weighted patterns)."""
    model = _model_or_404(db, org)
    sm = _stages(model).get(stage)
    if sm is None:
        raise HTTPException(404, "unknown stage")
    if not sm.ready:
        return {"stage": stage, "ready": False, "points": [], "axes": []}
    items: list[dict[str, Any]] = []
    rows = []
    for c, name in db.execute(
        select(CyclePattern, Account.name)
        .join(Account, Account.id == CyclePattern.account_id)
        .where(CyclePattern.model_id == model.id)
    ):
        p = (c.points or {}).get(str(stage))
        if p:
            rows.append(p["f"])
            items.append(
                {
                    "kind": "cycle",
                    "id": str(c.id),
                    "account_id": str(c.account_id),
                    "name": name,
                    "outcome": c.outcome,
                    "lost": c.outcome in POSITIVE,
                    "date": c.cycle_end.isoformat(),
                }
            )
    for m, name in db.execute(
        select(PatternMatch, Account.name)
        .join(Account, Account.id == PatternMatch.account_id)
        .where(PatternMatch.model_id == model.id, PatternMatch.stage == stage)
    ):
        now = (m.detail or {}).get("now")
        if now:
            rows.append(now["f"])
            items.append(
                {
                    "kind": "client",
                    "id": str(m.account_id),
                    "account_id": str(m.account_id),
                    "name": name,
                    "verdict": m.verdict,
                }
            )
    coords, axes = project(rows, sm)
    for item, (x, y) in zip(items, coords, strict=True):
        item["x"], item["y"] = x, y
    for axis in axes:
        for ld in axis["loadings"]:
            ld["label"] = SPEC[ld["feature"]].label
    return {"stage": stage, "ready": True, "points": items, "axes": axes}


@router.get("/patterns/strand/{account_id}")
def strand(
    account_id: uuid.UUID, db: Session = Depends(get_db), org: Org = Depends(current_org)
) -> dict[str, Any]:
    """One client's history as a chain of weekly activity, cut at each renewal decision. Counts only:
    no subjects, names or text."""
    acc = db.get(Account, account_id)
    if acc is None or acc.org_id != org.id:
        raise HTTPException(404, "account not found")
    since = datetime.now(UTC) - timedelta(days=STRAND_DAYS)
    week = func.date_trunc("week", Activity.occurred_at)
    weeks: dict[str, dict[str, float]] = defaultdict(lambda: {"emails_in": 0, "emails_out": 0, "meetings": 0})
    for w, kind, direction, n in db.execute(
        select(week, Activity.type, Activity.direction, func.count())
        .where(
            Activity.account_id == acc.id,
            Activity.occurred_at >= since,
            Activity.occurred_at <= datetime.now(UTC),
            Activity.match_method.not_in(UNRESOLVED),
            Activity.type.in_([ActivityType.email, ActivityType.meeting]),
        )
        .group_by(week, Activity.type, Activity.direction)
    ):
        key = w.date().isoformat()
        if kind == ActivityType.meeting:
            weeks[key]["meetings"] += int(n)
        elif direction == Direction.inbound:
            weeks[key]["emails_in"] += int(n)
        else:
            weeks[key]["emails_out"] += int(n)
    uweek = func.date_trunc("week", UsageDaily.date)
    usage = {
        w.date().isoformat(): round(float(avg), 2)
        for w, avg in db.execute(
            select(uweek, func.avg(UsageDaily.active_users))
            .where(UsageDaily.account_id == acc.id, UsageDaily.date >= since.date())
            .group_by(uweek)
        )
    }
    outcomes = list(
        db.scalars(
            select(RenewalOutcome)
            .where(RenewalOutcome.account_id == acc.id)
            .order_by(RenewalOutcome.renewal_date)
        )
    )
    # The renewal date as the latest training saw it through the as-of snapshot (the account row's value is
    # a UI cache that can already show the next term's date).
    model = latest_model(db, org.id)
    match = (
        db.scalar(
            select(PatternMatch).where(PatternMatch.model_id == model.id, PatternMatch.account_id == acc.id)
        )
        if model
        else None
    )
    renewal = match.renewal_date if match and match.renewal_date else acc.renewal_date
    keys = sorted(set(weeks) | set(usage))
    return {
        "account_id": str(acc.id),
        "name": acc.name,
        "today": datetime.now(UTC).date().isoformat(),
        "since": since.date().isoformat(),
        "renewal_date": renewal.isoformat() if renewal else None,
        "decisions": [
            {
                "date": o.renewal_date.isoformat(),
                "outcome": o.outcome.value,
                "lost": o.outcome.value in POSITIVE,
            }
            for o in outcomes
        ],
        "weeks": [
            {
                "week": k,
                **weeks.get(k, {"emails_in": 0, "emails_out": 0, "meetings": 0}),
                "users": usage.get(k),
            }
            for k in keys
        ],
    }
