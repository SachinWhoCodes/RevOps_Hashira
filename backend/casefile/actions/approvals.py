"""Approval service: people decide each line; the decision binds the exact change.

State machine for a change item:

    proposed -> approved | edited_approved | rejected
    approved | edited_approved -> queued -> executing -> succeeded | failed | conflict
    failed -(retry, same approval)-> queued
    conflict -(refresh: re-read the live value)-> proposed (needs a new decision)
    queued -(shadow mode)-> would_execute
    blocked: a rule stopped the line (money talk, no target); it can never execute.
    superseded: a newer plan replaced the line before anyone decided.

- Only the case owner or an admin may decide. Rows are locked while deciding, so a double click
  or two people approving at once produce one decision.
- The approval stores a hash of action + target + expected prior value + final payload. The
  executor recomputes it and refuses anything that differs.
- "Approve all Tier 1" never includes Tier 2 (customer emails are approved one by one).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.actions.spec import SPECS, EditError, apply_edit, approval_hash
from casefile.audit.log import append as audit
from casefile.db.models import (
    AccountCase,
    Approval,
    ApprovalChannel,
    AppUser,
    ChangeItem,
    ChangeItemStatus,
    ChangeSet,
    ChangeSetStatus,
    Decision,
    UserRole,
)

S = ChangeItemStatus


class ApprovalError(Exception):
    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


class LiveFieldReader(Protocol):
    def deal_next_step(self, deal_id: str) -> str | None: ...


def _case_for(session: Session, cs: ChangeSet) -> AccountCase:
    case = session.get(AccountCase, cs.case_id)
    assert case is not None
    return case


def can_decide(user: AppUser, case: AccountCase) -> bool:
    return user.org_id == case.org_id and (user.role == UserRole.admin or user.id == case.owner_user_id)


def _lock_item(session: Session, user: AppUser, item_id: uuid.UUID) -> tuple[ChangeItem, ChangeSet]:
    item = session.scalar(select(ChangeItem).where(ChangeItem.id == item_id).with_for_update())
    if item is None or item.org_id != user.org_id:
        raise ApprovalError("change not found", 404)
    cs = session.get(ChangeSet, item.change_set_id)
    assert cs is not None
    if not can_decide(user, _case_for(session, cs)):
        raise ApprovalError("only the account owner or an admin can decide on this change", 403)
    return item, cs


def latest_approval(session: Session, item_id: uuid.UUID) -> Approval | None:
    return session.scalar(
        select(Approval)
        .where(Approval.change_item_id == item_id)
        .order_by(Approval.decided_at.desc())
        .limit(1)
    )


def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def approve_item(
    session: Session,
    user: AppUser,
    item_id: uuid.UUID,
    *,
    edits: dict[str, Any] | None = None,
    comment: str | None = None,
    channel: ApprovalChannel = ApprovalChannel.web,
    queue: bool = True,
) -> ChangeItem:
    item, cs = _lock_item(session, user, item_id)
    if item.status == S.blocked:
        raise ApprovalError(f"this line is blocked: {item.external_result.get('blocked')}")
    if item.status != S.proposed:
        raise ApprovalError(f"this line was already decided ({item.status.value})")
    try:
        final = apply_edit(item.action_type, item.proposed_payload, edits) if edits else item.proposed_payload
    except EditError as exc:
        raise ApprovalError(str(exc), 422) from exc
    edited = final != item.proposed_payload
    digest = approval_hash(item.action_type, item.target_system, item.target_ref, item.before, final)
    approval = Approval(
        org_id=item.org_id,
        change_item_id=item.id,
        user_id=user.id,
        decision=Decision.approve,
        decided_at=datetime.now(UTC),  # app time: several decisions can share one transaction
        edited=edited,
        approved_payload_hash=digest,
        channel=channel,
        comment=comment,
    )
    session.add(approval)
    item.final_payload, item.payload_hash = final, digest
    item.status = S.edited_approved if edited else S.approved
    session.flush()
    audit(
        session,
        item.org_id,
        "change_item_edited_approved" if edited else "change_item_approved",
        actor_type="user",
        actor_id=str(user.id),
        entity_type="change_item",
        entity_id=str(item.id),
        evidence_ids=list(item.evidence_ids or []),
        approval_id=approval.id,
        details={
            "change_set_id": str(cs.id),
            "seq": item.seq,
            "type": item.action_type,
            "tier": item.tier,
            "payload_hash": digest,
            "edited_fields": _changed_fields(item.proposed_payload, final) if edited else [],
            "channel": channel.value,
        },
    )
    if queue:
        queue_item(session, item)
    rollup(session, cs)
    return item


def queue_item(session: Session, item: ChangeItem) -> None:
    if item.status in (S.approved, S.edited_approved):
        item.status = S.queued
        session.flush()


def reject_item(
    session: Session, user: AppUser, item_id: uuid.UUID, *, comment: str | None = None
) -> ChangeItem:
    item, cs = _lock_item(session, user, item_id)
    if item.status not in (S.proposed, S.blocked):
        raise ApprovalError(f"this line was already decided ({item.status.value})")
    approval = Approval(
        org_id=item.org_id,
        change_item_id=item.id,
        user_id=user.id,
        decision=Decision.reject,
        decided_at=datetime.now(UTC),
        comment=comment,
    )
    session.add(approval)
    item.status = S.rejected
    session.flush()
    audit(
        session,
        item.org_id,
        "change_item_rejected",
        actor_type="user",
        actor_id=str(user.id),
        entity_type="change_item",
        entity_id=str(item.id),
        approval_id=approval.id,
        details={"change_set_id": str(cs.id), "seq": item.seq, "type": item.action_type, "comment": comment},
    )
    rollup(session, cs)
    return item


def approve_tier1(
    session: Session,
    user: AppUser,
    change_set_id: uuid.UUID,
    *,
    channel: ApprovalChannel = ApprovalChannel.web,
) -> list[ChangeItem]:
    """Approve every undecided line of Tier 0-1 as proposed. Tier 2 lines are left for one-by-one review."""
    cs = session.get(ChangeSet, change_set_id)
    if cs is None or cs.org_id != user.org_id:
        raise ApprovalError("change set not found", 404)
    if not can_decide(user, _case_for(session, cs)):
        raise ApprovalError("only the account owner or an admin can decide on this change", 403)
    ids = list(
        session.scalars(
            select(ChangeItem.id)
            .where(ChangeItem.change_set_id == cs.id, ChangeItem.status == S.proposed, ChangeItem.tier <= 1)
            .order_by(ChangeItem.seq)
        )
    )
    return [
        approve_item(session, user, i, comment="approved with all Tier 1 lines", channel=channel) for i in ids
    ]


def retry_item(session: Session, user: AppUser, item_id: uuid.UUID) -> ChangeItem:
    item, cs = _lock_item(session, user, item_id)
    if item.status != S.failed:
        raise ApprovalError(f"only failed lines can be retried ({item.status.value})")
    if item.external_result.get("retryable") is False:
        raise ApprovalError("the outcome of the last attempt is unknown; check the target tool first")
    item.status = S.queued
    session.flush()
    audit(
        session,
        item.org_id,
        "execution_retry_requested",
        actor_type="user",
        actor_id=str(user.id),
        entity_type="change_item",
        entity_id=str(item.id),
        details={"seq": item.seq, "type": item.action_type, "last_error": item.external_result.get("error")},
    )
    rollup(session, cs)
    return item


def refresh_conflict(
    session: Session, user: AppUser, item_id: uuid.UUID, live: LiveFieldReader | None
) -> ChangeItem:
    """A field changed in the CRM after approval: read it again and ask for a fresh decision."""
    item, cs = _lock_item(session, user, item_id)
    if item.status != S.conflict:
        raise ApprovalError(f"only conflicting lines can be refreshed ({item.status.value})")
    if live is None or not item.target_ref.get("deal"):
        raise ApprovalError("cannot read the live CRM value right now", 503)
    old = dict(item.before)
    current = live.deal_next_step(item.target_ref["deal"])
    item.before = {
        **old,
        "hs_next_step": current,
        "read_at": datetime.now(UTC).isoformat(),
        "source": "hubspot_live",
    }
    item.status, item.final_payload, item.payload_hash = S.proposed, None, None
    session.flush()
    audit(
        session,
        item.org_id,
        "change_item_refreshed",
        actor_type="user",
        actor_id=str(user.id),
        entity_type="change_item",
        entity_id=str(item.id),
        details={"seq": item.seq, "expected_before": old.get("hs_next_step"), "found": current},
    )
    rollup(session, cs)
    return item


def rollup(session: Session, cs: ChangeSet) -> ChangeSetStatus:
    """Derive the change set's status from its lines."""
    counts: dict[ChangeItemStatus, int] = dict(
        session.execute(
            select(ChangeItem.status, func.count())
            .where(ChangeItem.change_set_id == cs.id)
            .group_by(ChangeItem.status)
        ).all()  # type: ignore[arg-type]
    )
    live = {k: v for k, v in counts.items() if k != S.superseded}
    total = sum(live.values())
    n = lambda *states: sum(live.get(s, 0) for s in states)  # noqa: E731
    if total == 0:
        status = ChangeSetStatus.superseded
    elif n(S.queued, S.executing):
        status = ChangeSetStatus.executing
    elif n(S.proposed) == total - n(S.blocked) and n(S.proposed):
        status = ChangeSetStatus.proposed
    elif n(S.proposed):
        status = ChangeSetStatus.partially_approved
    elif n(S.failed, S.conflict):
        status = ChangeSetStatus.failed
    elif n(S.approved, S.edited_approved):
        status = ChangeSetStatus.approved
    elif n(S.succeeded, S.would_execute):
        status = ChangeSetStatus.done
    else:
        status = ChangeSetStatus.rejected  # everything rejected or blocked
    if cs.status != status and cs.status != ChangeSetStatus.superseded:
        cs.status = status
        session.flush()
    return cs.status


def item_label(item: ChangeItem) -> str:
    return SPECS[item.action_type].label
