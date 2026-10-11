"""Phase 6.5: "Approve all Tier 1" from the Slack digest — bound to the exact lines, recorded as the
person who clicked, never Tier 2, and the listener acknowledges before it acts."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.actions.planner import plan_case
from casefile.actions.slack_approval import ACTION_APPROVE, digest_actions, handle_block_action
from casefile.ai.explain import explain_assessment, open_case_assessments
from casefile.bootstrap import ensure_connection
from casefile.cases.service import recompute_all
from casefile.db.models import (
    Account,
    AccountCase,
    Approval,
    ApprovalChannel,
    AppUser,
    CaseKind,
    CaseStatus,
    ChangeItem,
    ChangeItemStatus,
    ChangeSet,
    ConnectionKind,
    Org,
    Zone,
)
from casefile.executor.digest import build_digests
from casefile.slackbot.__main__ import process_envelope
from tests.conftest import TEST_TODAY

NOW = datetime(TEST_TODAY.year, TEST_TODAY.month, TEST_TODAY.day, 12, tzinfo=UTC)
SLACK_ADMIN = "U0ADMIN"


@pytest.fixture(scope="module")
def org(synced: Session) -> Iterator[Org]:
    org = synced.scalars(select(Org).where(Org.slug == "test-org")).one()
    recompute_all(synced, org, as_of=NOW)
    for aid in open_case_assessments(synced, org):
        explain_assessment(synced, aid, None)  # template explanations: no AI in tests
    ensure_connection(
        synced,
        org,
        ConnectionKind.hubspot,
        name="t",
        zone=Zone.write,
        config={"id_source": "crm_id", "portal_id": "1"},
    )
    ensure_connection(
        synced, org, ConnectionKind.slack, name="t", zone=Zone.write, config={"fallback_user_id": "U0"}
    )
    ensure_connection(
        synced, org, ConnectionKind.gmail, name="t", zone=Zone.write, config={"mailbox": "me@lumora.test"}
    )
    admin = synced.scalars(select(AppUser).where(AppUser.email == "admin@lumora.test")).one()
    admin.slack_user_id = SLACK_ADMIN
    synced.commit()
    yield org


def acme_plan(db: Session) -> tuple[AccountCase, ChangeSet]:
    case = db.scalars(
        select(AccountCase)
        .join(Account, Account.id == AccountCase.account_id)
        .where(
            Account.name == "Acme Corp",
            AccountCase.kind == CaseKind.risk,
            AccountCase.status == CaseStatus.open,
        )
    ).one()
    result = plan_case(db, case.id, gateway=None, live=None, now=NOW, force=True)
    assert result.change_set is not None, result.reason
    db.commit()
    return case, result.change_set


def click(value: str, user: str = SLACK_ADMIN) -> dict[str, Any]:
    return {
        "type": "block_actions",
        "user": {"id": user},
        "actions": [{"action_id": ACTION_APPROVE, "value": value}],
        "response_url": "https://hooks.slack.test/r/1",
    }


def items(db: Session, cs: ChangeSet) -> list[ChangeItem]:
    return list(
        db.scalars(select(ChangeItem).where(ChangeItem.change_set_id == cs.id).order_by(ChangeItem.seq))
    )


def test_digest_carries_the_buttons(synced: Session, org: Org) -> None:
    case, cs = acme_plan(synced)
    acts = digest_actions(synced, case)
    assert acts is not None and acts.change_set_id == cs.id and acts.tier1 >= 1 and acts.tier2 >= 1
    digest = next(d for d in build_digests(synced, org, "http://x") if str(case.id) in d.case_ids)
    buttons = [e for b in digest.blocks if b["type"] == "actions" for e in b["elements"]]
    approve = [b for b in buttons if b.get("action_id") == ACTION_APPROVE]
    assert approve and json.loads(approve[0]["value"])["cs"] == str(cs.id)
    assert any(b.get("url", "").startswith("http://x/accounts/") for b in buttons)
    notes = json.dumps(digest.blocks)
    assert "never approved from Slack" in notes  # the customer email stays in Casefile


def test_click_approves_tier1_as_the_person_who_clicked(synced: Session, org: Org) -> None:
    case, cs = acme_plan(synced)
    acts = digest_actions(synced, case)
    assert acts is not None
    reply = handle_block_action(synced, org, click(acts.button_value()))
    assert reply is not None and reply["replace_original"] and "Approved" in reply["text"]
    rows = items(synced, cs)
    tier1, tier2 = [i for i in rows if i.tier <= 1], [i for i in rows if i.tier >= 2]
    assert all(i.status in (ChangeItemStatus.queued, ChangeItemStatus.blocked) for i in tier1)
    assert all(i.status in (ChangeItemStatus.proposed, ChangeItemStatus.blocked) for i in tier2)
    approvals = synced.scalars(
        select(Approval).where(Approval.change_item_id.in_([i.id for i in tier1]))
    ).all()
    admin = synced.scalars(select(AppUser).where(AppUser.slack_user_id == SLACK_ADMIN)).one()
    assert approvals and all(a.channel == ApprovalChannel.slack and a.user_id == admin.id for a in approvals)
    # The same button again: those lines are decided now, so nothing more is approved.
    again = handle_block_action(synced, org, click(acts.button_value()))
    assert again is not None and not again.get("replace_original") and "Approved" not in again["text"]


def test_a_plan_changed_since_the_digest_is_refused(synced: Session, org: Org) -> None:
    case, cs = acme_plan(synced)
    acts = digest_actions(synced, case)
    assert acts is not None
    task = next(i for i in items(synced, cs) if i.action_type == "crm_task")
    task.proposed_payload = {**task.proposed_payload, "subject": "Something else"}
    synced.commit()
    reply = handle_block_action(synced, org, click(acts.button_value()))
    assert reply is not None and "changed since the digest" in reply["text"]
    assert all(
        i.status == ChangeItemStatus.proposed
        for i in items(synced, cs)
        if i.tier <= 1 and i.status != ChangeItemStatus.blocked
    )


def test_unlinked_slack_users_and_foreign_buttons_do_nothing(synced: Session, org: Org) -> None:
    case, cs = acme_plan(synced)
    acts = digest_actions(synced, case)
    assert acts is not None
    reply = handle_block_action(synced, org, click(acts.button_value(), user="U0STRANGER"))
    assert reply is not None and "not linked" in reply["text"]
    assert handle_block_action(synced, org, {"actions": [{"action_id": "something_else"}]}) is None
    assert all(
        i.status == ChangeItemStatus.proposed
        for i in items(synced, cs)
        if i.status != ChangeItemStatus.blocked
    )


def test_listener_acknowledges_before_it_acts() -> None:
    calls: list[str] = []
    ack, work = process_envelope(
        {"envelope_id": "e1", "type": "interactive", "payload": {"type": "block_actions"}},
        lambda p: calls.append("handled") or {"text": "ok"},
    )
    assert ack == {"envelope_id": "e1"} and calls == []  # nothing recorded before the ack is sent
    assert work is not None and work() == {"text": "ok"} and calls == ["handled"]
    ack, work = process_envelope({"envelope_id": "e2", "type": "events_api", "payload": {}}, lambda p: None)
    assert ack == {"envelope_id": "e2"} and work is None
