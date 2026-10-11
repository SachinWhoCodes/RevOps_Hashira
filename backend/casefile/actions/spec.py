"""Action types: what Casefile may propose, how risky each is, and what a person may edit.

Tiers follow the blueprint's action policy:

- 0: inside Casefile only (a "review manually" reminder) — nothing leaves the app.
- 1: CRM task, CRM note, deal next step, Slack DM to the owner — approval required, may be batched.
- 2: Gmail draft to a customer — approved one by one; drafts are created, never sent.
- Deal amount and stage are known action types that are disabled by default ("blocked").

Approval binds the *exact* change: ``approval_hash`` covers the action, its target IDs, the prior
value we expect to find (``before``) and the final payload. Changing any of them after approval
makes the executor refuse the item.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, Field, ValidationError

from casefile.ingest.util import payload_hash

EMAIL = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")


def _email(value: str) -> str:
    # Deliberately plain: synthetic contacts use the reserved .test domain, which strict validators reject.
    if not EMAIL.match(value):
        raise ValueError(f"not an email address: {value!r}")
    return value.lower()


Email = Annotated[str, AfterValidator(_email)]


class ReviewPayload(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class TaskPayload(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(max_length=5000)
    due_date: date
    priority: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"


class NotePayload(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


class NextStepPayload(BaseModel):
    value: str = Field(min_length=1, max_length=255)


class DraftPayload(BaseModel):
    to: list[Email] = Field(min_length=1, max_length=5)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=8000)


class SlackPayload(BaseModel):
    text: str = Field(min_length=1, max_length=3000)


@dataclass(frozen=True)
class ActionSpec:
    action_type: str
    label: str
    tier: int
    target_system: str
    payload_model: type[BaseModel]
    editable: frozenset[str]
    field_update: bool = False  # executor re-checks the live value against ``before``
    enabled_by_default: bool = True


SPECS: dict[str, ActionSpec] = {
    s.action_type: s
    for s in (
        ActionSpec("review_manually", "Review manually", 0, "casefile", ReviewPayload, frozenset({"text"})),
        ActionSpec(
            "crm_task",
            "CRM task",
            1,
            "hubspot",
            TaskPayload,
            frozenset({"subject", "body", "due_date", "priority"}),
        ),
        ActionSpec("crm_note", "CRM note", 1, "hubspot", NotePayload, frozenset({"body"})),
        ActionSpec(
            "crm_next_step",
            "Deal next step",
            1,
            "hubspot",
            NextStepPayload,
            frozenset({"value"}),
            field_update=True,
        ),
        ActionSpec("slack_dm", "Slack DM to owner", 1, "slack", SlackPayload, frozenset({"text"})),
        ActionSpec("gmail_draft", "Gmail draft", 2, "gmail", DraftPayload, frozenset({"subject", "body"})),
        # Known but blocked: money and pipeline fields stay human unless an admin enables them.
        ActionSpec(
            "crm_deal_amount",
            "Deal amount",
            1,
            "hubspot",
            NextStepPayload,
            frozenset(),
            field_update=True,
            enabled_by_default=False,
        ),
        ActionSpec(
            "crm_deal_stage",
            "Deal stage",
            1,
            "hubspot",
            NextStepPayload,
            frozenset(),
            field_update=True,
            enabled_by_default=False,
        ),
    )
}


class EditError(ValueError):
    pass


def validate_payload(action_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    spec = SPECS[action_type]
    try:
        return spec.payload_model.model_validate(payload).model_dump(mode="json")
    except ValidationError as exc:
        raise EditError(f"invalid {spec.label} payload: {exc.errors()[0]['msg']}") from exc


def apply_edit(action_type: str, proposed: dict[str, Any], edits: dict[str, Any]) -> dict[str, Any]:
    """Merge a person's edits into the proposed payload. Only editable fields may change."""
    spec = SPECS[action_type]
    locked = {k for k, v in edits.items() if k not in spec.editable and proposed.get(k) != v}
    if locked:
        raise EditError(f"{spec.label}: these fields cannot be edited: {', '.join(sorted(locked))}")
    return validate_payload(action_type, {**proposed, **edits})


def approval_hash(
    action_type: str,
    target_system: str,
    target_ref: dict[str, Any],
    before: dict[str, Any],
    payload: dict[str, Any],
) -> str:
    return payload_hash(
        {
            "action_type": action_type,
            "target_system": target_system,
            "target_ref": target_ref,
            "before": before,
            "payload": payload,
        }
    )


def marker(item_id: Any) -> str:
    """A single searchable token tying an external object to the change item that created it."""
    return f"cf{getattr(item_id, 'hex', str(item_id).replace('-', ''))}"
