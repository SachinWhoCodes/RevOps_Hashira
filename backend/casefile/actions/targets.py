"""Where a proposed change lands: the external IDs in HubSpot, Slack and Gmail.

Write connections (zone ``write``) carry non-secret settings only:

- hubspot: ``id_source`` = ``crm_id`` (the account was synced from this HubSpot portal, so our
  ``crm_id`` *is* the HubSpot ID) or ``external_ref`` (sandbox mirror: our synthetic accounts were
  copied into a test portal, and ``external_ref`` maps each one to the copy).
- slack: ``fallback_user_id`` — used when an owner has no Slack ID of their own. In the sandbox this
  is the workspace owner, and every DM says who it was meant for.
- gmail: ``mailbox`` — the address whose drafts folder receives the drafts.

IDs are resolved at proposal time and are part of what a person approves.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.db.models import (
    Account,
    AppUser,
    Connection,
    ConnectionKind,
    ConnectionStatus,
    Deal,
    ExternalRef,
    Zone,
)


def write_connection(session: Session, org_id: uuid.UUID, kind: ConnectionKind) -> Connection | None:
    return session.scalar(
        select(Connection).where(
            Connection.org_id == org_id,
            Connection.kind == kind,
            Connection.zone == Zone.write,
            Connection.status != ConnectionStatus.disabled,
        )
    )


def external_id(
    session: Session, org_id: uuid.UUID, entity_type: str, entity_id: uuid.UUID, system: str
) -> str | None:
    return session.scalar(
        select(ExternalRef.external_id).where(
            ExternalRef.org_id == org_id,
            ExternalRef.entity_type == entity_type,
            ExternalRef.entity_id == entity_id,
            ExternalRef.system == system,
        )
    )


def set_external_id(
    session: Session, org_id: uuid.UUID, entity_type: str, entity_id: uuid.UUID, system: str, value: str
) -> None:
    ref = session.scalar(
        select(ExternalRef).where(
            ExternalRef.org_id == org_id,
            ExternalRef.entity_type == entity_type,
            ExternalRef.entity_id == entity_id,
            ExternalRef.system == system,
        )
    )
    if ref is None:
        session.add(
            ExternalRef(
                org_id=org_id, entity_type=entity_type, entity_id=entity_id, system=system, external_id=value
            )
        )
    else:
        ref.external_id = value
    session.flush()


@dataclass(frozen=True)
class HubSpotTarget:
    company: str | None
    deal: str | None
    owner: str | None
    mirror: bool
    problem: str | None = None

    def ref(self) -> dict[str, Any]:
        return {"company": self.company, "deal": self.deal, "owner": self.owner, "mirror": self.mirror}


def hubspot_target(
    session: Session, account: Account, deal: Deal | None, owner: AppUser | None
) -> HubSpotTarget:
    conn = write_connection(session, account.org_id, ConnectionKind.hubspot)
    if conn is None:
        return HubSpotTarget(None, None, None, False, "no HubSpot write connection")
    if conn.config.get("id_source") == "external_ref":
        company = external_id(session, account.org_id, "account", account.id, "hubspot")
        deal_id = external_id(session, account.org_id, "deal", deal.id, "hubspot") if deal else None
        owner_id = (
            external_id(session, account.org_id, "user", owner.id, "hubspot") if owner else None
        ) or conn.config.get("default_owner_id")
        # The sandbox mirror may copy only the renewal deal (least-privilege write app); tasks and
        # notes then attach to the deal alone.
        problem = (
            None if (company or deal_id) else "this account has no HubSpot copy yet (run the sandbox mirror)"
        )
        return HubSpotTarget(company, deal_id, owner_id, True, problem)
    if account.crm_source != "hubspot":
        return HubSpotTarget(None, None, None, False, f"account comes from {account.crm_source}, not HubSpot")
    return HubSpotTarget(
        account.crm_id,
        deal.crm_id if deal else None,
        account.crm_owner_id or (owner.crm_owner_id if owner else None),
        False,
    )


@dataclass(frozen=True)
class SlackTarget:
    user_id: str | None
    intended_for: str | None
    substituted: bool
    problem: str | None = None


def slack_target(session: Session, org_id: uuid.UUID, owner: AppUser | None) -> SlackTarget:
    conn = write_connection(session, org_id, ConnectionKind.slack)
    if conn is None:
        return SlackTarget(None, None, False, "no Slack write connection")
    name = owner.name if owner else None
    own = owner.slack_user_id if owner else None
    if not own and owner is not None:
        own = external_id(session, org_id, "user", owner.id, "slack")
    if own:
        return SlackTarget(own, name, False)
    fallback = conn.config.get("fallback_user_id")
    if fallback:
        return SlackTarget(fallback, name, True)
    return SlackTarget(None, name, False, "the owner has no Slack user ID")


def gmail_mailbox(session: Session, org_id: uuid.UUID) -> tuple[str | None, str | None]:
    conn = write_connection(session, org_id, ConnectionKind.gmail)
    if conn is None:
        return None, "no Gmail write connection"
    return conn.config.get("mailbox"), None
