"""Sandbox setup (write zone): connect the test tools and mirror open-case accounts into HubSpot.

Our analysis runs on the synthetic company (labelled *simulated*). To prove the write path end
to end, the accounts with open cases are copied into the throwaway HubSpot portal — company, the
champion and economic buyer, and the open renewal deal — and ``external_ref`` maps each copy to
our record. Every copied company says in its description that it is a sandbox mirror of
synthetic data. Re-running is safe: existing copies are reused.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.actions.targets import external_id, set_external_id
from casefile.bootstrap import ensure_connection
from casefile.db.models import (
    Account,
    AccountCase,
    AppUser,
    CaseStatus,
    ConnectionKind,
    Contact,
    ContactStatus,
    Deal,
    DealType,
    Org,
    Zone,
)
from casefile.executor.writers.base import WriteRejected, read, send_once

HUBSPOT = "https://api.hubapi.com"
MIRROR_NOTE = "Casefile sandbox mirror of a synthetic account (simulated data, not a real customer)."


@dataclass
class MirrorReport:
    portal_id: str | None = None
    owner_id: str | None = None
    companies: dict[str, str] = field(default_factory=dict)
    contacts: int = 0
    deals: int = 0
    reused: int = 0
    notes: list[str] = field(default_factory=list)


def setup_slack(session: Session, org: Org, token: str, client: httpx.Client | None = None) -> dict[str, Any]:
    c = client or httpx.Client(
        base_url="https://slack.com/api", timeout=30, headers={"Authorization": f"Bearer {token}"}
    )
    auth = c.get("/auth.test").json()
    if not auth.get("ok"):
        raise RuntimeError(f"slack auth.test failed: {auth.get('error')}")
    owner_id = None
    cursor = None
    while owner_id is None:
        data = c.get("/users.list", params={"limit": 200, **({"cursor": cursor} if cursor else {})}).json()
        if not data.get("ok"):
            raise RuntimeError(f"slack users.list failed: {data.get('error')}")
        owner_id = next((m["id"] for m in data["members"] if m.get("is_primary_owner")), None)
        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    if owner_id is None:
        raise RuntimeError("no primary owner found in the Slack workspace")
    ensure_connection(
        session,
        org,
        ConnectionKind.slack,
        name="sandbox",
        zone=Zone.write,
        config={"team": auth.get("team"), "bot_user": auth.get("user_id"), "fallback_user_id": owner_id},
        secret_ref="SLACK_BOT_TOKEN",
    )
    return {"team": auth.get("team"), "fallback_user_id": owner_id}


def setup_gmail(session: Session, org: Org, client: httpx.Client) -> dict[str, Any]:
    profile = read(client, "GET", "/profile", "gmail profile")
    mailbox = profile["emailAddress"]
    ensure_connection(
        session,
        org,
        ConnectionKind.gmail,
        name="sandbox",
        zone=Zone.write,
        config={"mailbox": mailbox},
        secret_ref="WRITE_GOOGLE_REFRESH_TOKEN",
    )
    return {"mailbox": mailbox}


class _HubSpot:
    """Writes with the write token; looks things up with the read token (the write app is write-only)."""

    def __init__(self, token: str, read_token: str | None = None, client: httpx.Client | None = None) -> None:
        self.c = client or httpx.Client(
            base_url=HUBSPOT, timeout=30, headers={"Authorization": f"Bearer {token}"}
        )
        self.r = (
            httpx.Client(base_url=HUBSPOT, timeout=30, headers={"Authorization": f"Bearer {read_token}"})
            if read_token and client is None
            else self.c
        )

    def get(self, path: str, **params: Any) -> dict[str, Any] | None:
        r = self.r.get(path, params=params)
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise RuntimeError(f"hubspot GET {path}: {r.status_code} {r.text[:300]}")
        return r.json()  # type: ignore[no-any-return]

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return send_once(self.c, "POST", path, f"hubspot {path}", json=body).json()  # type: ignore[no-any-return]

    def put(self, path: str) -> None:
        send_once(self.c, "PUT", path, f"hubspot {path}")

    def search_one(self, kind: str, prop: str, value: str) -> str | None:
        data = read(
            self.r,
            "POST",
            f"/crm/v3/objects/{kind}/search",
            f"hubspot {kind} search",
            json={
                "filterGroups": [{"filters": [{"propertyName": prop, "operator": "EQ", "value": value}]}],
                "limit": 1,
            },
        )
        results = data.get("results", [])
        return str(results[0]["id"]) if results else None

    def associate(self, from_kind: str, from_id: str, to_kind: str, to_id: str) -> None:
        # v4 "default" association: no association type IDs to get wrong.
        self.put(f"/crm/v4/objects/{from_kind}/{from_id}/associations/default/{to_kind}/{to_id}")


def _ensure(
    hs: _HubSpot,
    session: Session,
    org: Org,
    entity_type: str,
    entity_id: Any,
    kind: str,
    search: tuple[str, str] | None,
    props: dict[str, Any],
    report: MirrorReport,
) -> tuple[str, bool]:
    known = external_id(session, org.id, entity_type, entity_id, "hubspot")
    if known and hs.get(f"/crm/v3/objects/{kind}/{known}") is not None:
        report.reused += 1
        return known, False
    found = hs.search_one(kind, *search) if search else None
    if found:
        report.reused += 1
        new_id = found
    else:
        new_id = str(hs.post(f"/crm/v3/objects/{kind}", {"properties": props})["id"])
    set_external_id(session, org.id, entity_type, entity_id, "hubspot", new_id)
    return new_id, not found


def mirror_open_cases(
    session: Session,
    org: Org,
    token: str,
    *,
    read_token: str | None = None,
    client: httpx.Client | None = None,
    limit: int | None = None,
    owner_id: str | None = None,
) -> MirrorReport:
    hs = _HubSpot(token, read_token, client)
    report = MirrorReport()
    details = hs.get("/account-info/v3/details") or {}
    report.portal_id = str(details.get("portalId")) if details.get("portalId") else None
    report.owner_id = owner_id
    if report.owner_id is None:
        try:
            owners = (hs.get("/crm/v3/owners", limit=100) or {}).get("results", [])
            report.owner_id = str(owners[0]["id"]) if owners else None
        except RuntimeError as exc:  # the write app may lack crm.objects.owners.read
            report.notes.append(f"owner lookup failed ({str(exc)[:60]}…); tasks will be unassigned")
    ensure_connection(
        session,
        org,
        ConnectionKind.hubspot,
        name="sandbox",
        zone=Zone.write,
        config={
            "id_source": "external_ref",
            "portal_id": report.portal_id,
            "default_owner_id": report.owner_id,
            "note": "sandbox mirror of synthetic accounts",
        },
        secret_ref="WRITE_HUBSPOT_TOKEN",
    )
    if report.owner_id:
        for user in session.scalars(select(AppUser).where(AppUser.org_id == org.id)):
            set_external_id(session, org.id, "user", user.id, "hubspot", report.owner_id)

    accounts = list(
        session.scalars(
            select(Account)
            .join(AccountCase, AccountCase.account_id == Account.id)
            .where(AccountCase.org_id == org.id, AccountCase.status == CaseStatus.open)
            .distinct()
            .order_by(Account.name)
        )
    )
    can_create_companies = True
    for account in accounts[:limit]:
        company_id: str | None = None
        if can_create_companies:
            try:
                company_props = {
                    "name": account.name,
                    "domain": account.domains[0] if account.domains else None,
                    "description": MIRROR_NOTE,
                    "seats_purchased": account.seats_purchased,
                    "renewal_date": account.renewal_date.isoformat() if account.renewal_date else None,
                }
                company_id, _ = _ensure(
                    hs,
                    session,
                    org,
                    "account",
                    account.id,
                    "companies",
                    ("domain", account.domains[0]) if account.domains else None,
                    {k: v for k, v in company_props.items() if v is not None},
                    report,
                )
                report.companies[account.name] = company_id
                contacts = session.scalars(
                    select(Contact).where(
                        Contact.account_id == account.id,
                        Contact.role.in_(["champion", "economic_buyer"]),
                        Contact.status == ContactStatus.active,
                    )
                )
                for contact in contacts:
                    first, _, last = (contact.name or "").partition(" ")
                    contact_id, created = _ensure(
                        hs,
                        session,
                        org,
                        "contact",
                        contact.id,
                        "contacts",
                        ("email", contact.email),
                        {
                            "email": contact.email,
                            "firstname": first,
                            "lastname": last,
                            "jobtitle": contact.title or "",
                        },
                        report,
                    )
                    hs.associate("contacts", contact_id, "companies", company_id)
                    report.contacts += int(created)
            except WriteRejected as exc:
                if "not authorised" not in str(exc):
                    raise
                # Least privilege: the executor's app may only create tasks, notes and deals.
                can_create_companies = False
                report.notes.append(
                    "write app cannot create companies/contacts; mirrored renewal deals only "
                    "(tasks and notes attach to the deal)"
                )
        deal = session.scalar(
            select(Deal)
            .where(
                Deal.account_id == account.id, Deal.deal_type == DealType.renewal, Deal.is_closed.is_(False)
            )
            .order_by(Deal.close_date)
            .limit(1)
        )
        if deal is not None:
            deal_props = {
                "dealname": deal.name,
                "pipeline": "default",
                "dealstage": deal.stage or "appointmentscheduled",
                "closedate": deal.close_date.isoformat() if deal.close_date else None,
                "hs_next_step": deal.next_step or "",
                "description": MIRROR_NOTE,
                "hubspot_owner_id": report.owner_id,
            }
            deal_id, created = _ensure(
                hs,
                session,
                org,
                "deal",
                deal.id,
                "deals",
                ("dealname", deal.name),
                {k: v for k, v in deal_props.items() if v is not None},
                report,
            )
            if company_id:
                hs.associate("deals", deal_id, "companies", company_id)
            report.deals += int(created)
        session.commit()
    return report
