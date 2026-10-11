"""Per-organisation action policy (stored in ``policy``; admins may change tiers or disable actions)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.actions.spec import SPECS
from casefile.db.models import Policy


@dataclass(frozen=True)
class Rule:
    tier: int
    enabled: bool


def ensure_policies(session: Session, org_id: uuid.UUID) -> None:
    existing = set(session.scalars(select(Policy.action_type).where(Policy.org_id == org_id)))
    for spec in SPECS.values():
        if spec.action_type not in existing:
            session.add(
                Policy(
                    org_id=org_id,
                    action_type=spec.action_type,
                    tier=spec.tier,
                    enabled=spec.enabled_by_default,
                )
            )
    session.flush()


def policy_for(session: Session, org_id: uuid.UUID) -> dict[str, Rule]:
    ensure_policies(session, org_id)
    rules = {
        p.action_type: Rule(p.tier, p.enabled)
        for p in session.scalars(select(Policy).where(Policy.org_id == org_id))
    }
    # A customer-facing email is never batch-approvable, whatever the table says.
    if "gmail_draft" in rules:
        rules["gmail_draft"] = Rule(max(2, rules["gmail_draft"].tier), rules["gmail_draft"].enabled)
    return rules
