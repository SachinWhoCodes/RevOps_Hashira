"""Phase 4: planner, approvals and the executor's safety guarantees (blueprint executor-safety plan).

Writers are in-memory fakes; nothing here touches a real tool. The live tools are exercised by the
sandbox run, not by tests.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from casefile.actions.approvals import (
    ApprovalError,
    approve_item,
    approve_tier1,
    latest_approval,
    refresh_conflict,
    reject_item,
    retry_item,
)
from casefile.actions.planner import plan_case
from casefile.ai.explain import explain_assessment, open_case_assessments
from casefile.ai.gateway import AiGateway
from casefile.audit import log as audit_log
from casefile.audit.log import verify_chain
from casefile.bootstrap import ensure_connection, upsert_user
from casefile.cases.service import recompute_all
from casefile.db.models import (
    Account,
    AccountCase,
    AppUser,
    AuditLog,
    CaseKind,
    CaseStatus,
    ChangeItem,
    ChangeItemStatus,
    ChangeSet,
    ConnectionKind,
    LlmCache,
    Org,
    UserRole,
    Zone,
)
from casefile.executor.runner import Executor
from casefile.executor.writers.base import WriteRejected, WriteUnknown
from tests.conftest import TEST_TODAY
from tests.fake_ai import FakeClient, response
from tests.fake_writers import FakeWriter, fake_writers

NOW = datetime(TEST_TODAY.year, TEST_TODAY.month, TEST_TODAY.day, 12, tzinfo=UTC)
S = ChangeItemStatus
SUITE = Path(__file__).resolve().parents[2] / "evals" / "datasets" / "injection_suite.jsonl"


class FakeLive:
    def __init__(self) -> None:
        self.values: dict[str, str | None] = {}

    def deal_next_step(self, deal_id: str) -> str | None:
        return self.values.get(deal_id)


@dataclass
class World:
    db: Session
    org: Org
    admin: AppUser
    live: FakeLive


@pytest.fixture(scope="module")
def world(synced: Session) -> Iterator[World]:
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
    synced.commit()
    admin = synced.scalars(select(AppUser).where(AppUser.email == "admin@lumora.test")).one()
    yield World(synced, org, admin, FakeLive())


def case_for(w: World, name: str = "Acme Corp", kind: CaseKind = CaseKind.risk) -> AccountCase:
    return w.db.scalars(
        select(AccountCase)
        .join(Account, Account.id == AccountCase.account_id)
        .where(Account.name == name, AccountCase.kind == kind, AccountCase.status == CaseStatus.open)
    ).one()


def fresh_plan(w: World, case: AccountCase | None = None, gateway: AiGateway | None = None) -> ChangeSet:
    result = plan_case(w.db, (case or case_for(w)).id, gateway=gateway, live=w.live, now=NOW, force=True)
    assert result.change_set is not None, result.reason
    w.db.commit()
    return result.change_set


def fresh_gateway(w: World, client: FakeClient) -> AiGateway:
    """Each AI test gets its own answers: drop cached responses from earlier tests."""
    w.db.execute(delete(LlmCache))
    return AiGateway(w.db, w.org.id, client=client)


def items(w: World, cs: ChangeSet) -> dict[str, ChangeItem]:
    return {
        i.action_type: i
        for i in w.db.scalars(
            select(ChangeItem).where(ChangeItem.change_set_id == cs.id).order_by(ChangeItem.seq)
        )
    }


def audit_actions(w: World, item: ChangeItem) -> list[str]:
    return list(
        w.db.scalars(select(AuditLog.action).where(AuditLog.entity_id == str(item.id)).order_by(AuditLog.seq))
    )


# ---------------------------------------------------------------- planner


def test_plan_for_acme_follows_the_playbook(world: World) -> None:
    cs = fresh_plan(world)
    by_type = items(world, cs)
    assert cs.playbook_id in {
        "champion_departure",
        "champion_silent_renewal_near",
        "usage_decline",
        "meeting_gap",
    }
    assert list(by_type)[:2] == ["crm_task", "gmail_draft"]
    assert by_type["gmail_draft"].tier == 2 and by_type["crm_task"].tier == 1
    assert all(i.status == S.proposed for i in by_type.values()), {k: v.status for k, v in by_type.items()}
    task = by_type["crm_task"]
    assert task.why and task.evidence_ids, "every line carries the verified claims and evidence behind it"
    assert task.target_ref["company"] and task.target_ref["deal"]
    draft = by_type["gmail_draft"].proposed_payload
    assert draft["to"] and draft["to"][0].endswith("@acme-corp.test")
    assert cs.notes["draft_source"] == "template"
    if "crm_next_step" in by_type:
        assert by_type["crm_next_step"].before["source"] == "hubspot_live"
    dm = by_type["slack_dm"]
    assert dm.target_ref["user_id"] == "U0" and dm.target_ref["substituted"] is True
    assert "meant for Priya Sharma" in dm.proposed_payload["text"]


def test_planning_twice_for_the_same_assessment_returns_the_same_plan(world: World) -> None:
    first = fresh_plan(world)
    again = plan_case(world.db, first.case_id, live=world.live, now=NOW)
    assert again.change_set is not None and again.change_set.id == first.id and not again.created


def test_replanning_supersedes_undecided_lines(world: World) -> None:
    old = fresh_plan(world)
    old_items = items(world, old)
    new = fresh_plan(world)
    world.db.refresh(old)
    assert old.status.value == "superseded"
    assert all(world.db.get(ChangeItem, i.id).status == S.superseded for i in old_items.values())  # type: ignore[union-attr]
    assert new.id != old.id


def test_ai_draft_with_money_talk_is_blocked(world: World) -> None:
    from casefile.actions.drafter import DraftOut

    client = FakeClient(
        lambda kw: response(
            DraftOut(subject="Renewal", body="Hi Dana,\n\nWe can offer a 20% discount.\n\nBest,\nPriya"),
            model="claude-opus-5-5",
        )
    )
    cs = fresh_plan(world, gateway=fresh_gateway(world, client))
    draft = items(world, cs)["gmail_draft"]
    assert draft.status == S.blocked and "money talk stays human" in draft.external_result["blocked"]
    with pytest.raises(ApprovalError, match="blocked"):
        approve_item(world.db, world.admin, draft.id)


def test_ai_draft_that_invents_a_name_is_retried_then_templated(world: World) -> None:
    from casefile.actions.drafter import DraftOut

    client = FakeClient(
        lambda kw: response(
            DraftOut(
                subject="Check-in",
                body="Hi Dana,\n\nRahul Verma at Datawise asked me to write.\n\nBest,\nPriya",
            ),
            model="claude-opus-5-5",
        )
    )
    cs = fresh_plan(world, gateway=fresh_gateway(world, client))
    assert len(client.calls) == 2  # one retry with the checker's reasons
    assert "CHECKER FEEDBACK" in client.calls[1]["messages"][0]["content"]
    assert cs.notes["draft_source"] == "template"
    assert "Datawise" not in items(world, cs)["gmail_draft"].proposed_payload["body"]


def test_good_ai_draft_is_used(world: World) -> None:
    from casefile.actions.drafter import DraftOut

    body = (
        "Hi Dana,\n\nI'd like to hear how things are going for the team before the renewal. "
        "Would you have 30 minutes next week for a short call?\n\nBest,\nPriya"
    )
    client = FakeClient(
        lambda kw: response(DraftOut(subject="Quick check-in", body=body), model="claude-opus-5-5")
    )
    cs = fresh_plan(world, gateway=fresh_gateway(world, client))
    assert cs.notes["draft_source"] == "ai", cs.notes
    assert items(world, cs)["gmail_draft"].proposed_payload["body"] == body
    sent = client.calls[0]
    assert sent["model"] == "claude-opus-5-5"
    assert "dana.whitfield@acme-corp.test" not in sent["messages"][0]["content"]  # masked before sending


# ---------------------------------------------------------------- approvals


def test_only_owner_or_admin_can_decide(world: World) -> None:
    cs = fresh_plan(world)
    other = upsert_user(
        world.db, world.org, email="rahul@lumora.test", name="Rahul Verma", role=UserRole.owner
    )
    with pytest.raises(ApprovalError) as exc:
        approve_item(world.db, other, items(world, cs)["crm_task"].id)
    assert exc.value.status_code == 403


def test_approve_all_tier1_excludes_customer_email(world: World) -> None:
    cs = fresh_plan(world)
    approved = approve_tier1(world.db, world.admin, cs.id)
    world.db.commit()
    by_type = items(world, cs)
    assert {i.action_type for i in approved} == {t for t, i in by_type.items() if i.tier <= 1}
    assert by_type["gmail_draft"].status == S.proposed
    assert all(i.status == S.queued for t, i in by_type.items() if t != "gmail_draft")
    assert cs.status.value == "executing"


def test_edit_changes_the_approved_hash_and_locked_fields_are_refused(world: World) -> None:
    cs = fresh_plan(world)
    task = items(world, cs)["crm_task"]
    with pytest.raises(ApprovalError) as exc:
        approve_item(
            world.db, world.admin, items(world, cs)["gmail_draft"].id, edits={"to": ["x@evil.example"]}
        )
    assert exc.value.status_code == 422
    approve_item(world.db, world.admin, task.id, edits={"subject": "Call Dana this week"})
    world.db.commit()
    assert task.status == S.queued and (task.final_payload or {})["subject"] == "Call Dana this week"
    assert "change_item_edited_approved" in audit_actions(world, task)


def test_reject_records_a_decision_and_never_executes(world: World) -> None:
    cs = fresh_plan(world)
    note = items(world, cs)["crm_note"]
    reject_item(world.db, world.admin, note.id, comment="not useful")
    world.db.commit()
    writers = fake_writers()
    assert Executor(world.db, writers).process(note.id) is None
    assert writers["hubspot"].writes == []


# ---------------------------------------------------------------- executor safety


def test_happy_path_writes_once_and_audits(world: World) -> None:
    cs = fresh_plan(world)
    task = items(world, cs)["crm_task"]
    approve_item(world.db, world.admin, task.id)
    world.db.commit()
    writers = fake_writers()
    outcome = Executor(world.db, writers).process(task.id)
    assert outcome is not None and outcome.status == "succeeded"
    assert len(writers["hubspot"].writes) == 1
    assert writers["hubspot"].writes[0].marker == f"cf{task.id.hex}"
    assert audit_actions(world, task)[-3:] == [
        "change_item_approved",
        "execution_started",
        "execution_succeeded",
    ]
    assert verify_chain(world.db, world.org.id)["ok"]


def test_approve_twice_gives_one_write(world: World) -> None:
    cs = fresh_plan(world)
    task = items(world, cs)["crm_task"]
    approve_item(world.db, world.admin, task.id)
    with pytest.raises(ApprovalError, match="already decided"):
        approve_item(world.db, world.admin, task.id)
    world.db.commit()
    writers = fake_writers()
    ex = Executor(world.db, writers)
    ex.process(task.id)
    assert ex.process(task.id) is None  # no longer queued
    task.status = S.queued  # simulate a stray re-queue (bug or manual DB edit)
    world.db.commit()
    assert ex.process(task.id).status == "duplicate_suppressed"  # type: ignore[union-attr]
    assert len(writers["hubspot"].writes) == 1


def test_crash_after_write_is_reconciled_without_duplicate(world: World) -> None:
    cs = fresh_plan(world)
    note = items(world, cs)["crm_note"]
    approve_item(world.db, world.admin, note.id)
    world.db.commit()
    writers = fake_writers()

    def crash(*_: Any) -> None:
        raise RuntimeError("executor killed between the external call and the commit")

    with pytest.raises(RuntimeError):
        Executor(world.db, writers, after_write=crash).process(note.id)
    world.db.rollback()
    world.db.refresh(note)
    assert note.status == S.executing
    outcomes = Executor(world.db, writers).reconcile(older_than=timedelta(0))
    assert [o.status for o in outcomes if o.item_id == str(note.id)] == ["succeeded"]
    world.db.refresh(note)
    assert note.status == S.succeeded and len(writers["hubspot"].writes) == 1
    assert "execution_reconciled" in audit_actions(world, note)


def test_crash_before_write_is_requeued_and_written_once(world: World) -> None:
    cs = fresh_plan(world)
    task = items(world, cs)["crm_task"]
    approve_item(world.db, world.admin, task.id)
    world.db.commit()
    writers = fake_writers()
    writers["hubspot"].fail = RuntimeError("killed before the call")
    with pytest.raises(RuntimeError):
        Executor(world.db, writers).process(task.id)
    world.db.rollback()
    outcomes = Executor(world.db, writers).reconcile(older_than=timedelta(0))
    assert [o.status for o in outcomes if o.item_id == str(task.id)] == ["requeued"]
    assert Executor(world.db, writers).process(task.id).status == "succeeded"  # type: ignore[union-attr]
    assert len(writers["hubspot"].writes) == 1


def test_changed_crm_value_becomes_conflict_then_needs_a_new_approval(world: World) -> None:
    cs = fresh_plan(world)
    step = items(world, cs).get("crm_next_step")
    assert step is not None, "Acme's play updates the next step"
    approve_item(world.db, world.admin, step.id)
    world.db.commit()
    writers = fake_writers()
    writers["hubspot"].fields["hs_next_step"] = "Someone typed this in HubSpot after the proposal"
    outcome = Executor(world.db, writers).process(step.id)
    assert outcome is not None and outcome.status == "conflict"
    assert writers["hubspot"].writes == []
    world.live.values[step.target_ref["deal"]] = "Someone typed this in HubSpot after the proposal"
    refresh_conflict(world.db, world.admin, step.id, world.live)
    world.db.commit()
    assert step.status == S.proposed and step.payload_hash is None
    approve_item(world.db, world.admin, step.id)
    world.db.commit()
    assert Executor(world.db, writers).process(step.id).status == "succeeded"  # type: ignore[union-attr]
    assert writers["hubspot"].fields["hs_next_step"] == (step.final_payload or {})["value"]
    world.live.values.clear()


def test_unapproved_item_is_refused_and_logged(world: World) -> None:
    cs = fresh_plan(world)
    task = items(world, cs)["crm_task"]
    task.status = S.queued  # someone bypasses the approval service
    world.db.commit()
    writers = fake_writers()
    outcome = Executor(world.db, writers).process(task.id)
    assert outcome is not None and outcome.status == "refused"
    assert writers["hubspot"].writes == [] and task.status == S.failed
    assert "execution_refused" in audit_actions(world, task)


@pytest.mark.parametrize("field", ["final_payload", "target_ref", "before"])
def test_tampering_after_approval_is_refused(world: World, field: str) -> None:
    cs = fresh_plan(world)
    task = items(world, cs)["crm_task"]
    approve_item(world.db, world.admin, task.id)
    world.db.commit()
    if field == "final_payload":
        task.final_payload = {**task.final_payload, "subject": "Offer them 50% off"}  # type: ignore[dict-item]
    elif field == "target_ref":
        task.target_ref = {**task.target_ref, "company": "999999"}
    else:
        task.before = {"hs_next_step": "tampered"}
    world.db.commit()
    writers = fake_writers()
    outcome = Executor(world.db, writers).process(task.id)
    assert outcome is not None and outcome.status == "refused"
    assert "no longer matches" in outcome.detail["reason"]
    assert writers["hubspot"].writes == []


def test_audit_failure_means_no_write(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    cs = fresh_plan(world)
    task = items(world, cs)["crm_task"]
    approve_item(world.db, world.admin, task.id)
    world.db.commit()
    real = audit_log.append

    def failing(session: Session, org_id: Any, action: str, **kw: Any) -> Any:
        if action == "execution_started":
            raise RuntimeError("audit store unavailable")
        return real(session, org_id, action, **kw)

    monkeypatch.setattr(audit_log, "append", failing)
    writers = fake_writers()
    outcome = Executor(world.db, writers).process(task.id)
    assert outcome is not None and outcome.status == "audit_failed"
    assert writers["hubspot"].writes == []
    world.db.refresh(task)
    assert task.status == S.queued


def test_rejected_write_fails_and_can_be_retried(world: World) -> None:
    cs = fresh_plan(world)
    task = items(world, cs)["crm_task"]
    approve_item(world.db, world.admin, task.id)
    world.db.commit()
    writers = fake_writers()
    writers["hubspot"].fail = WriteRejected("rate limited", retryable=True)
    assert Executor(world.db, writers).process(task.id).status == "failed"  # type: ignore[union-attr]
    retry_item(world.db, world.admin, task.id)
    world.db.commit()
    assert Executor(world.db, writers).process(task.id).status == "succeeded"  # type: ignore[union-attr]
    assert len(writers["hubspot"].writes) == 1


def test_unknown_outcome_is_resolved_by_marker(world: World) -> None:
    cs = fresh_plan(world)
    dm = items(world, cs)["slack_dm"]
    approve_item(world.db, world.admin, dm.id)
    world.db.commit()
    writers = fake_writers()
    writers["slack"].fail = WriteUnknown("timeout after sending")
    outcome = Executor(world.db, writers).process(dm.id)
    assert outcome is not None and outcome.status == "failed"  # not found by marker → safe to retry
    assert dm.external_result["retryable"] is True
    unverifiable = FakeWriter("slack", verifiable=False)
    retry_item(world.db, world.admin, dm.id)
    world.db.commit()
    unverifiable.fail = WriteUnknown("timeout after sending")
    outcome = Executor(world.db, {"slack": unverifiable}).process(dm.id)
    assert outcome is not None and outcome.status == "unknown"
    with pytest.raises(ApprovalError, match="unknown"):
        retry_item(world.db, world.admin, dm.id)  # a person must check Slack first


def test_shadow_mode_writes_nothing(world: World) -> None:
    cs = fresh_plan(world)
    approve_tier1(world.db, world.admin, cs.id)
    world.db.commit()
    writers = fake_writers()
    ex = Executor(world.db, writers, shadow=True)
    for item in items(world, cs).values():
        if item.status == S.queued:
            ex.process(item.id)
    assert all(not w.writes for w in writers.values())
    assert {i.status for i in items(world, cs).values()} <= {S.would_execute, S.proposed}


def test_full_change_set_reaches_every_target(world: World) -> None:
    cs = fresh_plan(world)
    by_type = items(world, cs)
    approve_tier1(world.db, world.admin, cs.id)
    approve_item(world.db, world.admin, by_type["gmail_draft"].id, edits={"subject": "Checking in"})
    world.db.commit()
    writers = fake_writers()
    ex = Executor(world.db, writers)
    for item in by_type.values():
        ex.process(item.id)
    world.db.refresh(cs)
    assert cs.status.value == "done", {t: i.status for t, i in items(world, cs).items()}
    assert {w.system for w in writers.values() if w.writes} >= {"hubspot", "gmail", "slack"}
    assert writers["gmail"].writes[0].payload["subject"] == "Checking in"


# ---------------------------------------------------------------- injection end to end


def test_injection_suite_end_to_end_makes_zero_unapproved_writes(world: World) -> None:
    from casefile.ai.injection import scan_injections
    from casefile.db.models import Activity, ActivityType, Direction, MatchMethod

    case = case_for(world)
    account = world.db.get(Account, case.account_id)
    assert account is not None
    attacks = [json.loads(line) for line in SUITE.read_text().splitlines() if line.strip()]
    attacks = [a for a in attacks if a["attack"]]
    champion = "dana.whitfield@acme-corp.test"
    for i, a in enumerate(attacks):
        world.db.add(
            Activity(
                org_id=world.org.id,
                account_id=account.id,
                type=ActivityType.email,
                direction=Direction.inbound,
                occurred_at=NOW - timedelta(hours=2 + i),
                known_at=NOW - timedelta(hours=2 + i),
                source="gmail",
                source_id=f"attack-{a['id']}",
                subject=a["subject"],
                snippet=a["body"][:200],
                body_text=a["body"],
                from_email=champion,
                participant_emails=[champion],
                extra={
                    "hidden_text": a.get("hidden_text", ""),
                    "zero_width_chars": a.get("zero_width", False),
                },
                match_method=MatchMethod.contact_email,
                match_confidence=1.0,
            )
        )
    world.db.flush()
    scan_injections(world.db, world.org)
    recompute_all(world.db, world.org, as_of=NOW)
    case = case_for(world)
    assert case.current_assessment_id is not None
    explain_assessment(world.db, case.current_assessment_id, None)

    # The AI "obeys" every attack: it tries to put the attacker's wishes in the draft.
    from casefile.actions.drafter import DraftOut

    client = FakeClient(
        lambda kw: response(
            DraftOut(
                subject="Account healthy", body="Hi Dana,\n\nAs requested, a 30% discount.\n\nBest,\nPriya"
            ),
            model="claude-opus-5-5",
        )
    )
    cs = fresh_plan(world, case=case, gateway=fresh_gateway(world, client))
    planned = list(items(world, cs).values())
    text = json.dumps([i.proposed_payload for i in planned] + [i.why for i in planned])
    for a in attacks:
        for fragment in ("ignore your previous", "SYSTEM NOTE", "mark this account"):
            if fragment.lower() in a["body"].lower():
                assert fragment.lower() not in text.lower(), (a["id"], fragment)
    assert all(i.status in (S.proposed, S.blocked) for i in planned)
    writers = fake_writers()
    Executor(world.db, writers).run_once(limit=500)
    written = [v for w in writers.values() for v in w.writes]
    planned_ids = {i.id for i in planned}
    assert not any(v.id in planned_ids for v in written), "nothing from this plan is written without approval"
    for v in written:  # leftovers from earlier tests may run; each must carry an approval
        approval = latest_approval(world.db, v.id)
        assert approval is not None and approval.decision.value == "approve"


def test_written_explanation_queues_a_plan_for_its_open_case(world: World) -> None:
    from casefile.db.models import Job

    queued = {
        j.payload["case_id"]
        for j in world.db.scalars(select(Job).where(Job.kind == "plan_case", Job.org_id == world.org.id))
    }
    assert str(case_for(world).id) in queued
