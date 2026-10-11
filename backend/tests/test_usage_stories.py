"""The generated usage stories do what they claim when Casefile's real usage rules read them."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from casefile.bootstrap import ensure_org
from casefile.db.models import Account, EntityType, FieldHistory, UsageDaily
from casefile.signals.base import run_signals
from casefile.synth.usage_stories import STORY_ORDER, UNMATCHED_KEY, Company, generate
from casefile.timeline.snapshot import AccountHistory

TODAY = date(2026, 10, 11)
USAGE_SIGNALS = {"usage_decline", "seat_saturation", "new_team_adoption"}
EXPECTED = {
    "sudden_drop": {"usage_decline"},
    "slow_slide": {"usage_decline"},
    "new_team": {"new_team_adoption"},
    "deploys_collapse": {"usage_decline"},
    "went_dark": {"usage_decline"},
    "seats_nearly_full": {"seat_saturation"},
    "growing": set(),
    "holiday_dip_recovered": set(),
    "steady": set(),
    "spiky_but_flat": set(),
    "new_customer_ramp": set(),
    "tiny_account": set(),
    "no_usage_data": set(),
}


def test_every_usage_story_fires_or_stays_quiet_as_designed(db: Session) -> None:
    org = ensure_org(
        db, slug="usage-stories", name="Hosting Co", timezone="UTC", internal_domains=["host.test"]
    )
    companies = [Company(f"Customer {i}", f"c{i}.test", True) for i in range(len(STORY_ORDER))]
    rows, stories = generate(companies, TODAY)
    assert sorted(stories.values()) == sorted(STORY_ORDER)  # one company per story
    assert any(r["account_key"] == UNMATCHED_KEY for r in rows)
    accounts = {}
    for c in companies:
        seats = 50 if stories[c.key] == "seats_nearly_full" else None
        acc = Account(
            org_id=org.id,
            crm_source="t",
            crm_id=c.key,
            name=c.name,
            domains=[c.key],
            seats_purchased=seats,
            created_in_source_at=datetime(2024, 1, 1, tzinfo=UTC),  # a customer for years
        )
        db.add(acc)
        accounts[c.key] = acc
    db.flush()
    if seat_key := next(k for k, s in stories.items() if s == "seats_nearly_full"):
        # the generator sizes seats per company; give the CRM the same plan size it used
        plan = max(int(r["seats_used"]) for r in rows if r["account_key"] == seat_key)
        seats = round(plan / 0.96)
        accounts[seat_key].seats_purchased = seats
        # The snapshot reads CRM fields from their dated history, as a CRM sync would have written it.
        db.add(
            FieldHistory(
                org_id=org.id,
                entity_type=EntityType.account,
                entity_id=accounts[seat_key].id,
                field="seats_purchased",
                value=seats,
                valid_from=datetime(2024, 1, 1, tzinfo=UTC),
                source="crm",
            )
        )
    db.add_all(
        UsageDaily(
            org_id=org.id,
            account_id=accounts[str(r["account_key"])].id if r["account_key"] in accounts else None,
            external_account_key=str(r["account_key"]),
            date=date.fromisoformat(str(r["date"])),
            active_users=int(r["active_users"]),  # type: ignore[call-overload]
            key_actions=int(r["key_actions"]),  # type: ignore[call-overload]
            seats_used=int(r["seats_used"]),  # type: ignore[call-overload]
            departments=json.loads(str(r["departments"])),
            source="usage",
        )
        for r in rows
    )
    db.flush()
    as_of = datetime(TODAY.year, TODAY.month, TODAY.day, 12, tzinfo=UTC)
    fired = {}
    for key, acc in accounts.items():
        snap = AccountHistory.load(db, acc.id).as_of(as_of)
        assert snap is not None
        fired[stories[key]] = {h.type for h in run_signals(snap)} & USAGE_SIGNALS
    assert fired == EXPECTED
    # The kind of decline matches the story, not just "some decline".
    kinds = {}
    for key, acc in accounts.items():
        snap = AccountHistory.load(db, acc.id).as_of(as_of)
        assert snap is not None
        for h in run_signals(snap):
            if h.type == "usage_decline":
                kinds[stories[key]] = h.facts["decline_kind"]
    assert kinds == {
        "sudden_drop": "sudden",
        "slow_slide": "gradual",
        "deploys_collapse": "sudden",
        "went_dark": "sudden",
    }
