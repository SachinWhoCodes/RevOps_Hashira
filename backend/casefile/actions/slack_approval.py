"""Approving from Slack (Phase 6.5): "Approve all Tier 1" buttons in the morning digest.

Two processes share this module and neither gains a new write path:

- the executor (which already holds the Slack bot token) puts the buttons in the digest;
- the ``slackbot`` process (app zone, Socket Mode app token only) receives a click, records the
  decision exactly as the web app would — same ``approve_item``, ``channel=slack``, audited — and
  answers through Slack's ``response_url``. The executor still performs every write (rule R1).

The button binds the exact change (rule R4): its value carries a fingerprint of the Tier 1 lines as
they were when the digest was built (each line's approval hash). If anything changed since — a
re-plan, an edit, a line decided in the web app — the click is refused and points to Casefile.
Customer email drafts (Tier 2) are never approvable from Slack, only "Open in Casefile".
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.actions.approvals import ApprovalError, approve_tier1
from casefile.actions.spec import approval_hash
from casefile.db.models import (
    Account,
    AccountCase,
    ApprovalChannel,
    AppUser,
    ChangeItem,
    ChangeItemStatus,
    ChangeSet,
    ChangeSetStatus,
    Org,
)

ACTION_APPROVE = "casefile_approve_tier1"
# Plans still waiting for decisions (same as the planner's; kept here so the executor, which builds
# the digest, does not import the planner and its AI modules).
OPEN_SET = (ChangeSetStatus.proposed, ChangeSetStatus.partially_approved)


@dataclass(frozen=True)
class DigestActions:
    change_set_id: uuid.UUID
    tier1: int
    tier2: int
    fingerprint: str

    def button_value(self) -> str:
        return json.dumps({"cs": str(self.change_set_id), "fp": self.fingerprint})


def _proposed(session: Session, cs: ChangeSet) -> list[ChangeItem]:
    return list(
        session.scalars(
            select(ChangeItem)
            .where(ChangeItem.change_set_id == cs.id, ChangeItem.status == ChangeItemStatus.proposed)
            .order_by(ChangeItem.seq)
        )
    )


def fingerprint(items: list[ChangeItem]) -> str:
    """Short hash binding the exact Tier 1 lines a person saw (each line's approval hash)."""
    parts = [
        f"{i.id}:{approval_hash(i.action_type, i.target_system, i.target_ref, i.before, i.proposed_payload)}"
        for i in sorted(items, key=lambda i: i.seq)
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def digest_actions(session: Session, case: AccountCase) -> DigestActions | None:
    cs = session.scalar(
        select(ChangeSet)
        .where(ChangeSet.case_id == case.id, ChangeSet.status.in_(OPEN_SET))
        .order_by(ChangeSet.created_at.desc())
        .limit(1)
    )
    if cs is None:
        return None
    proposed = _proposed(session, cs)
    tier1 = [i for i in proposed if i.tier <= 1]
    tier2 = [i for i in proposed if i.tier >= 2]
    if not proposed:
        return None
    return DigestActions(cs.id, len(tier1), len(tier2), fingerprint(tier1))


def digest_blocks(
    session: Session, case: AccountCase, account: Account, text_line: str, link: str
) -> list[dict[str, Any]]:
    """One case in the digest: the summary line, then the buttons this case allows."""
    blocks: list[dict[str, Any]] = [{"type": "section", "text": {"type": "mrkdwn", "text": text_line}}]
    acts = digest_actions(session, case)
    buttons: list[dict[str, Any]] = []
    if acts and acts.tier1:
        buttons.append(
            {
                "type": "button",
                "action_id": ACTION_APPROVE,
                "style": "primary",
                "text": {
                    "type": "plain_text",
                    "text": f"Approve {acts.tier1} Tier 1 action{'s' * (acts.tier1 > 1)}",
                },
                "value": acts.button_value(),
                "confirm": {
                    "title": {"type": "plain_text", "text": "Approve these actions?"},
                    "text": {
                        "type": "mrkdwn",
                        "text": f"Casefile will create the CRM task, note and next step and the owner DM for "
                        f"*{account.name}* exactly as proposed. Customer emails are not included.",
                    },
                    "confirm": {"type": "plain_text", "text": "Approve"},
                    "deny": {"type": "plain_text", "text": "Cancel"},
                },
            }
        )
    buttons.append(
        {
            "type": "button",
            "action_id": "casefile_open",
            "text": {"type": "plain_text", "text": "Open in Casefile"},
            "url": link,
        }
    )
    blocks.append({"type": "actions", "elements": buttons})
    if acts and acts.tier2:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"{acts.tier2} customer email draft{'s' * (acts.tier2 > 1)} need"
                        f"{'s' * (acts.tier2 == 1)} your review in Casefile — never approved from Slack.",
                    }
                ],
            }
        )
    return blocks


# ---------------------------------------------------------------- handling a click (slackbot process)


def _reply(text: str) -> dict[str, Any]:
    return {"replace_original": False, "response_type": "ephemeral", "text": text}


def handle_block_action(session: Session, org: Org, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Record a Slack approval. Returns the message to send to ``response_url`` (None: not ours)."""
    actions = payload.get("actions") or []
    action = next((a for a in actions if a.get("action_id") == ACTION_APPROVE), None)
    if action is None:
        return None
    slack_user = (payload.get("user") or {}).get("id")
    user = session.scalar(
        select(AppUser).where(AppUser.org_id == org.id, AppUser.slack_user_id == slack_user)
    )
    if user is None:
        return _reply("Your Slack account is not linked to a Casefile user, so nothing was approved.")
    try:
        value = json.loads(action.get("value") or "{}")
        cs = session.get(ChangeSet, uuid.UUID(value["cs"]))
    except (ValueError, KeyError, TypeError):
        return _reply("This button is not valid any more. Open the case in Casefile.")
    if cs is None or cs.org_id != org.id or cs.status not in OPEN_SET:
        return _reply("This plan is no longer open. Open the case in Casefile.")
    tier1 = [i for i in _proposed(session, cs) if i.tier <= 1]
    if not tier1 or fingerprint(tier1) != value.get("fp"):
        return _reply(
            "This plan changed since the digest was sent (re-planned, edited or partly decided), so "
            "nothing was approved. Open it in Casefile to review the current version."
        )
    case = session.get(AccountCase, cs.case_id)
    account = session.get(Account, case.account_id) if case else None
    try:
        approved = approve_tier1(session, user, cs.id, channel=ApprovalChannel.slack)
    except ApprovalError as exc:
        session.rollback()
        return _reply(f"Nothing was approved: {exc}")
    session.commit()
    name = account.name if account else "this account"
    return {
        "replace_original": True,
        "text": (
            f"Approved {len(approved)} Tier 1 action{'s' * (len(approved) > 1)} for *{name}* "
            f"(by {user.name or user.email} from Slack). The executor will carry them out; customer "
            "emails still need review in Casefile."
        ),
    }
