"""Renewal outcome labels, derived from CRM renewal-deal history (works the same on a real CRM).

| Renewal deal                                  | Label       |
|-----------------------------------------------|-------------|
| Lost                                          | churned     |
| Won, amount lower than the previous won deal  | downgraded  |
| Won, closed more than 14 days after the date  | late        |
| Won otherwise                                 | renewed     |

"The date" is the first close date ever recorded on the deal (what the team planned), not the final
one. A person can correct any label on the review screen; corrected labels (``label_source =
"human"``) are never overwritten by a re-derivation.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.audit.log import append as audit
from casefile.db.models import (
    Deal,
    DealType,
    FieldHistory,
    Org,
    RenewalOutcome,
    RenewalOutcomeKind,
)

LATE_GRACE_DAYS = 14
# Revenue was lost: these count as positives when we ask "did Casefile see it coming?"
POSITIVE = {RenewalOutcomeKind.churned.value, RenewalOutcomeKind.downgraded.value}


@dataclass
class DeriveReport:
    counts: dict[str, int] = field(default_factory=dict)
    created: int = 0
    updated: int = 0
    kept_human: int = 0


def _as_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _as_decimal(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except ArithmeticError:
        return None


def classify(
    *, won: bool, amount: Decimal | None, previous: Decimal | None, planned: date, closed: date
) -> RenewalOutcomeKind:
    if not won:
        return RenewalOutcomeKind.churned
    if amount is not None and previous is not None and amount < previous:
        return RenewalOutcomeKind.downgraded
    if closed > planned + timedelta(days=LATE_GRACE_DAYS):
        return RenewalOutcomeKind.late
    return RenewalOutcomeKind.renewed


def derive_outcomes(session: Session, org: Org, *, today: date | None = None) -> DeriveReport:
    from casefile.ingest.mapping import org_mapping

    mapping = org_mapping(session, org.id)["hubspot"]["deal"]
    won_stages, lost_stages = set(mapping["won_stages"]), set(mapping["lost_stages"])
    deals = list(session.scalars(select(Deal).where(Deal.org_id == org.id, Deal.account_id.is_not(None))))
    history: dict[uuid.UUID, dict[str, list[tuple[datetime, Any]]]] = defaultdict(lambda: defaultdict(list))
    ids = [d.id for d in deals]
    for start in range(0, len(ids), 2000):
        for row in session.execute(
            select(FieldHistory.entity_id, FieldHistory.field, FieldHistory.valid_from, FieldHistory.value)
            .where(
                FieldHistory.entity_id.in_(ids[start : start + 2000]),
                FieldHistory.field.in_(["close_date", "stage"]),
            )
            .order_by(FieldHistory.valid_from)
        ):
            history[row.entity_id][row.field].append((row.valid_from, row.value))

    by_account: dict[uuid.UUID, list[Deal]] = defaultdict(list)
    for d in deals:
        if d.close_date and (d.stage in won_stages or d.stage in lost_stages):
            by_account[d.account_id].append(d)  # type: ignore[index]
    existing = {
        (o.account_id, o.renewal_date): o
        for o in session.scalars(select(RenewalOutcome).where(RenewalOutcome.org_id == org.id))
    }
    report = DeriveReport()
    for account_id, closed in by_account.items():
        closed.sort(key=lambda d: d.close_date or date.min)
        previous_won: Decimal | None = None
        for d in closed:
            won = d.stage in won_stages
            if d.deal_type == DealType.renewal and (
                today is None or (d.close_date and d.close_date <= today)
            ):
                planned = next(
                    (v for v in (_as_date(val) for _, val in history[d.id]["close_date"]) if v), d.close_date
                )
                assert planned is not None and d.close_date is not None
                kind = classify(
                    won=won, amount=d.amount, previous=previous_won, planned=planned, closed=d.close_date
                )
                decided = next(
                    (at for at, val in reversed(history[d.id]["stage"]) if val in won_stages | lost_stages),
                    None,
                )
                report.counts[kind.value] = report.counts.get(kind.value, 0) + 1
                stored = existing.get((account_id, planned))
                if stored is None:
                    session.add(
                        RenewalOutcome(
                            org_id=org.id,
                            account_id=account_id,
                            deal_id=d.id,
                            renewal_date=planned,
                            outcome=kind,
                            arr_before=previous_won,
                            arr_after=d.amount if won else Decimal(0),
                            label_source="derived",
                            decided_at=decided,
                        )
                    )
                    report.created += 1
                elif stored.label_source == "human":
                    report.kept_human += 1
                elif stored.outcome != kind or stored.deal_id != d.id:
                    stored.outcome, stored.deal_id, stored.decided_at = kind, d.id, decided
                    stored.arr_before, stored.arr_after = previous_won, d.amount if won else Decimal(0)
                    report.updated += 1
            if won and d.amount is not None:
                previous_won = d.amount
    session.flush()
    return report


def correct_label(
    session: Session, outcome_id: uuid.UUID, kind: RenewalOutcomeKind, *, user_id: uuid.UUID, note: str | None
) -> RenewalOutcome:
    row = session.get(RenewalOutcome, outcome_id)
    if row is None:
        raise LookupError("outcome not found")
    old = row.outcome
    row.outcome, row.label_source = kind, "human"
    session.flush()
    audit(
        session,
        row.org_id,
        "outcome_label_corrected",
        actor_type="user",
        actor_id=str(user_id),
        entity_type="renewal_outcome",
        entity_id=str(row.id),
        details={
            "from": old.value,
            "to": kind.value,
            "note": note,
            "renewal_date": row.renewal_date.isoformat(),
        },
    )
    return row
