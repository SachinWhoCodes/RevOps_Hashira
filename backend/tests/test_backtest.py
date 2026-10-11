"""Phase 5: outcome labels, leakage checks on real synced history, and the back-test runner."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.backtest.metrics import auc, bootstrap, precision_at_k, precision_recall, wilson
from casefile.backtest.outcomes import classify, correct_label, derive_outcomes
from casefile.backtest.runner import BacktestConfig, run_backtest
from casefile.cases.service import assess
from casefile.db.models import (
    Account,
    Activity,
    ActivityType,
    AppUser,
    Contact,
    Direction,
    EntityType,
    FieldHistory,
    MatchMethod,
    Org,
    RenewalOutcome,
    RenewalOutcomeKind,
    UsageDaily,
)
from casefile.timeline.snapshot import AccountHistory

K = RenewalOutcomeKind


def _org(s: Session) -> Org:
    return s.scalars(select(Org).where(Org.slug == "test-org")).one()


# ---------------------------------------------------------------- labels


@pytest.mark.parametrize(
    ("won", "amount", "previous", "closed_days_late", "expected"),
    [
        (False, None, Decimal(100), 0, K.churned),
        (True, Decimal(70), Decimal(100), 0, K.downgraded),
        (True, Decimal(100), Decimal(100), 20, K.late),
        (True, Decimal(110), Decimal(100), 14, K.renewed),  # 14 days is within the grace period
        (True, Decimal(100), None, 0, K.renewed),
    ],
)
def test_classify(
    won: bool, amount: Decimal | None, previous: Decimal | None, closed_days_late: int, expected: K
) -> None:
    planned = date(2026, 3, 1)
    got = classify(
        won=won,
        amount=amount,
        previous=previous,
        planned=planned,
        closed=planned + timedelta(days=closed_days_late),
    )
    assert got == expected


def test_derive_outcomes_is_idempotent_and_keeps_human_labels(synced: Session) -> None:
    org = _org(synced)
    first = derive_outcomes(synced, org)
    assert first.created > 0 and first.counts.get("churned", 0) > 0
    row = synced.scalars(select(RenewalOutcome).where(RenewalOutcome.org_id == org.id)).first()
    assert row is not None
    admin = synced.scalars(select(AppUser).where(AppUser.email == "admin@lumora.test")).one()
    corrected = K.late if row.outcome != K.late else K.renewed
    correct_label(synced, row.id, corrected, user_id=admin.id, note="checked with the owner")
    again = derive_outcomes(synced, org)
    assert again.created == 0 and again.kept_human >= 1
    synced.refresh(row)
    assert row.outcome == corrected and row.label_source == "human"


# ---------------------------------------------------------------- leakage (plan Phase 5 item 2)


def _acme_history(s: Session) -> AccountHistory:
    acme = s.scalars(select(Account).where(Account.name == "Acme Corp", Account.org_id == _org(s).id)).one()
    return AccountHistory.load(s, acme.id)


def _signature(h: AccountHistory, t: datetime) -> tuple[Any, ...]:
    snap = h.as_of(t)
    assert snap is not None
    a = assess(snap)
    return (a.score.risk, a.score.opportunity, [hit.to_dict() for hit in a.hits])


def test_canary_future_records_never_change_a_past_score(synced: Session) -> None:
    h = _acme_history(synced)
    t = datetime(2026, 8, 1, 12, tzinfo=UTC)
    before = _signature(h, t)
    later = t + timedelta(hours=1)
    contact = h.contacts[0]
    deal = next(d for d in h.deals if d.deal_type.value == "renewal")
    # Future facts that would change the score if they leaked into the past.
    h.fields.add(contact.id, "status", later, "left")
    h.fields.add(deal.id, "stage", later, "closedlost")
    h.fields.add(deal.id, "next_step", later, "")
    h.activities.append(
        Activity(
            id=uuid.uuid4(),
            type=ActivityType.email,
            direction=Direction.inbound,
            source="gmail",
            occurred_at=later,
            known_at=later,
            cancelled_at=None,
            subject="We are cancelling",
            snippet="We will not renew.",
            from_email=contact.email,
            participant_emails=[contact.email],
            contact_ids=[contact.id],
            extra={"bounce": True},
            match_method=MatchMethod.contact_email,
            match_confidence=1.0,
            injection_flag=False,
        )
    )
    h.activities.sort(key=lambda a: a.occurred_at)
    local_day = t.astimezone(h.tz).date()
    h.usage += [
        UsageDaily(
            date=local_day + timedelta(days=i), active_users=0, key_actions=0, seats_used=0, departments={}
        )
        for i in range(0, 60)
    ]
    assert _signature(h, t) == before


def test_a_deal_lost_later_shows_its_stage_at_the_time(synced: Session) -> None:
    org = _org(synced)
    derive_outcomes(synced, org)
    lost = synced.scalars(
        select(RenewalOutcome).where(RenewalOutcome.org_id == org.id, RenewalOutcome.outcome == K.churned)
    ).first()
    assert lost is not None and lost.decided_at is not None and lost.deal_id is not None
    h = AccountHistory.load(synced, lost.account_id)
    then = h.as_of(lost.decided_at - timedelta(days=30))
    after = h.as_of(lost.decided_at + timedelta(minutes=1))
    assert then is not None and after is not None
    deal_then = next(d for d in then.deals if d.id == lost.deal_id)
    deal_after = next(d for d in after.deals if d.id == lost.deal_id)
    assert not deal_then.is_closed and deal_then.stage != "closedlost"
    assert deal_after.stage == "closedlost"


def test_a_contact_marked_left_later_looks_active_before(synced: Session) -> None:
    """The CRM records "left" on a date; a snapshot one day earlier must still show the person active."""
    org = _org(synced)
    contact = synced.scalars(
        select(Contact)
        .join(Account, Account.id == Contact.account_id)
        .where(Account.name == "Acme Corp", Contact.org_id == org.id, Contact.role == "champion")
    ).first()
    assert contact is not None and contact.account_id is not None
    marked = datetime(2026, 9, 1, 9, tzinfo=UTC)
    synced.add(
        FieldHistory(
            org_id=org.id,
            entity_type=EntityType.contact,
            entity_id=contact.id,
            field="status",
            value="left",
            valid_from=marked,
            source="test",
        )
    )
    synced.flush()
    h = AccountHistory.load(synced, contact.account_id)
    before = h.as_of(marked - timedelta(days=1))
    after = h.as_of(marked + timedelta(minutes=1))
    assert before is not None and after is not None
    assert before.contact(contact.id).status == "active"  # type: ignore[union-attr]
    assert after.contact(contact.id).status == "left"  # type: ignore[union-attr]


# ---------------------------------------------------------------- metrics and runner


def test_metrics() -> None:
    assert auc([0.9, 0.8, 0.1], [True, False, False]) == 1.0
    assert auc([1, 1], [True, False]) == 0.5
    assert precision_recall([True, True, False], [True, False, True]) == (0.5, 0.5)
    assert precision_at_k([3, 2, 1], [False, True, True], 2) == 0.5
    lo, hi = bootstrap([1.0, 0.0] * 20, lambda s: sum(s) / len(s)) or (None, None)
    assert lo is not None and hi is not None and lo < 0.5 < hi
    # Four out of four is not certainty: the Wilson range stays wide for small n.
    w_lo, w_hi = wilson(4, 4) or (0.0, 0.0)
    assert 0.45 < w_lo < 0.55 and w_hi == 1.0
    assert (wilson(0, 148) or (1.0, 1.0))[1] < 0.03


def test_backtest_runs_on_the_small_company(synced: Session) -> None:
    org = _org(synced)
    derive_outcomes(synced, org)
    run = run_backtest(synced, org, BacktestConfig(min_history_days=60))
    m = run.metrics
    assert run.status == "done" and m["label"] == "simulated"
    assert m["events"]["scored"] > 0
    block = m["horizon"]["30"]["all"]
    assert {"n", "positives", "auc", "precision", "recall"} <= set(block)
    assert block["auc"]["n"] == block["n"]  # every number carries its n
    assert "negative_tone" in m["excluded_signals"]  # the AI has read almost none of these emails
    assert m["flag_threshold"]["default"] == 40
