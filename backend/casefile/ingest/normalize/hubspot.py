"""HubSpot CRM v3 objects → accounts, contacts, deals, CRM activities and field history.

History comes from ``propertiesWithHistory``. When a property arrives without history (for
example from a list call that didn't request it), its current value is recorded as of the
object's last-modified time, so the as-of loader still has a value — never a future one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert

from casefile.connectors.base import RawItem
from casefile.db.models import (
    Account,
    Activity,
    ActivityType,
    AppUser,
    Contact,
    ContactRole,
    ContactStatus,
    Deal,
    DealType,
    Direction,
    EntityType,
    MatchMethod,
)
from casefile.ingest.context import NormalizeContext
from casefile.ingest.text import html_to_text, snippet
from casefile.ingest.util import parse_date, parse_decimal_str, parse_ts

SOURCE = "hubspot"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
ROLE_PRIORITY = {"champion": 0, "economic_buyer": 1, "user": 2, "unknown": 3}


def _props(p: dict[str, Any]) -> dict[str, Any]:
    return p.get("properties") or {}


def _modified_at(p: dict[str, Any]) -> datetime:
    props = _props(p)
    return (
        parse_ts(p.get("updatedAt"))
        or parse_ts(props.get("hs_lastmodifieddate"))
        or parse_ts(props.get("lastmodifieddate"))
        or parse_ts(p.get("createdAt"))
        or parse_ts(props.get("createdate"))
        or EPOCH
    )


def _created_at(p: dict[str, Any]) -> datetime | None:
    return parse_ts(_props(p).get("createdate")) or parse_ts(p.get("createdAt"))


def _history(p: dict[str, Any], prop: str | None, convert: Any = lambda v: v) -> list[tuple[datetime, Any]]:
    """History entries for one property as (timestamp, converted value)."""
    if not prop:
        return []
    raw = (p.get("propertiesWithHistory") or {}).get(prop)
    if raw:
        out = []
        for entry in raw:
            ts = parse_ts(entry.get("timestamp"))
            if ts is not None:
                out.append((ts, convert(entry.get("value"))))
        return out
    if prop in _props(p):
        # No history: the current value is only known to be true from the last modification on.
        return [(_modified_at(p), convert(_props(p).get(prop)))]
    return []


def _first_company(p: dict[str, Any]) -> str | None:
    results = ((p.get("associations") or {}).get("companies") or {}).get("results") or []
    return str(results[0]["id"]) if results else None


def _int_or_none(v: Any) -> int | None:
    try:
        return int(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _date_iso(v: Any) -> str | None:
    d = parse_date(v)
    return d.isoformat() if d else None


# ---------------------------------------------------------------- owners


def owners(ctx: NormalizeContext, items: list[RawItem]) -> None:
    for item in items:
        p = item.payload
        email = (p.get("email") or "").lower()
        if not email:
            continue
        name = " ".join(x for x in [p.get("firstName"), p.get("lastName")] if x)
        stmt = insert(AppUser).values(
            org_id=ctx.org_id, email=email, name=name, crm_owner_id=str(p["id"]), is_active=False
        )
        ctx.session.execute(
            stmt.on_conflict_do_update(
                index_elements=["org_id", "email"],
                set_={"name": stmt.excluded.name, "crm_owner_id": stmt.excluded.crm_owner_id},
            )
        )


# ---------------------------------------------------------------- companies


def companies(ctx: NormalizeContext, items: list[RawItem]) -> None:
    cmap = ctx.mapping["hubspot"]["company"]
    for item in items:
        p = item.payload
        props = _props(p)
        domains: list[str] = []
        for d in [props.get("domain"), *(props.get("hs_additional_domains") or "").split(";")]:
            d = (d or "").strip().lower()
            if d and d not in domains:
                domains.append(d)
        owner = props.get("hubspot_owner_id") or None
        row = {
            "org_id": ctx.org_id,
            "crm_source": SOURCE,
            "crm_id": str(p["id"]),
            "name": props.get("name") or f"HubSpot company {p['id']}",
            "domains": domains,
            "crm_owner_id": owner,
            "owner_user_id": ctx.user_id_for_owner(owner),
            "seats_purchased": _int_or_none(props.get(cmap.get("seats_purchased") or "")),
            "renewal_date": parse_date(props.get(cmap.get("renewal_date") or "")),
            "created_in_source_at": _created_at(p),
        }
        stmt = insert(Account).values(row)
        update = {k: stmt.excluded[k] for k in row if k not in {"org_id", "crm_source", "crm_id"}}
        account_id: uuid.UUID = ctx.session.execute(
            stmt.on_conflict_do_update(
                index_elements=["org_id", "crm_source", "crm_id"], set_=update
            ).returning(Account.id)
        ).scalar_one()
        ctx.remember_account(SOURCE, str(p["id"]), account_id)
        ctx.touched_accounts.add(account_id)

        created = _created_at(p) or _modified_at(p)
        ctx.history(EntityType.account, account_id, "exists", [(created, True)], SOURCE)
        ctx.history(EntityType.account, account_id, "name", _history(p, "name"), SOURCE)
        ctx.history(EntityType.account, account_id, "domains", [(created, domains)], SOURCE)
        ctx.history(EntityType.account, account_id, "owner", _history(p, "hubspot_owner_id"), SOURCE)
        ctx.history(
            EntityType.account,
            account_id,
            "seats_purchased",
            _history(p, cmap.get("seats_purchased"), _int_or_none),
            SOURCE,
        )
        ctx.history(
            EntityType.account,
            account_id,
            "renewal_date",
            _history(p, cmap.get("renewal_date"), _date_iso),
            SOURCE,
        )
        if cmap.get("arr"):
            ctx.history(
                EntityType.account, account_id, "arr", _history(p, cmap["arr"], parse_decimal_str), SOURCE
            )


# ---------------------------------------------------------------- contacts


def _role(ctx: NormalizeContext, raw: Any) -> str:
    values = ctx.mapping["hubspot"]["contact"]["role_values"]
    roles = [values.get(part.strip(), "unknown") for part in str(raw or "").split(";") if part.strip()]
    return min(roles, key=lambda r: ROLE_PRIORITY[r]) if roles else "unknown"


def _status(ctx: NormalizeContext, raw: Any) -> str:
    return ctx.mapping["hubspot"]["contact"]["status_values"].get(str(raw or "").strip(), "active")


def contacts(ctx: NormalizeContext, items: list[RawItem]) -> None:
    cmap = ctx.mapping["hubspot"]["contact"]
    for item in items:
        p = item.payload
        props = _props(p)
        email = (props.get("email") or "").strip().lower()
        if not email:
            continue
        company_ref = str(props.get("associatedcompanyid") or "") or _first_company(p)
        account_id = ctx.account_id_for_crm(SOURCE, company_ref)
        row = {
            "org_id": ctx.org_id,
            "account_id": account_id,
            "crm_source": SOURCE,
            "crm_id": str(p["id"]),
            "crm_account_ref": company_ref,
            "email": email,
            "name": " ".join(x for x in [props.get("firstname"), props.get("lastname")] if x),
            "title": props.get("jobtitle") or None,
            "role": ContactRole(_role(ctx, props.get(cmap["role_property"]))),
            "status": ContactStatus(_status(ctx, props.get(cmap["status_property"]))),
            "origin": "crm",
            "created_in_source_at": _created_at(p),
        }
        stmt = insert(Contact).values(row)
        update = {k: stmt.excluded[k] for k in row if k not in {"org_id", "email"}}
        contact_id: uuid.UUID = ctx.session.execute(
            stmt.on_conflict_do_update(index_elements=["org_id", "email"], set_=update).returning(Contact.id)
        ).scalar_one()
        if account_id:
            ctx.touched_accounts.add(account_id)
        created = _created_at(p) or _modified_at(p)
        ctx.history(EntityType.contact, contact_id, "exists", [(created, True)], SOURCE)
        ctx.history(
            EntityType.contact,
            contact_id,
            "role",
            _history(p, cmap["role_property"], lambda v: _role(ctx, v)),
            SOURCE,
        )
        ctx.history(
            EntityType.contact,
            contact_id,
            "status",
            _history(p, cmap["status_property"], lambda v: _status(ctx, v)),
            SOURCE,
        )
        ctx.history(EntityType.contact, contact_id, "title", _history(p, "jobtitle"), SOURCE)


# ---------------------------------------------------------------- deals


def deals(ctx: NormalizeContext, items: list[RawItem]) -> None:
    dmap = ctx.mapping["hubspot"]["deal"]
    won, lost = set(dmap["won_stages"]), set(dmap["lost_stages"])
    for item in items:
        p = item.payload
        props = _props(p)
        company_ref = _first_company(p)
        account_id = ctx.account_id_for_crm(SOURCE, company_ref)
        stage = props.get("dealstage")
        next_step_date_prop = dmap.get("next_step_date_property")
        row = {
            "org_id": ctx.org_id,
            "account_id": account_id,
            "crm_source": SOURCE,
            "crm_id": str(p["id"]),
            "crm_account_ref": company_ref,
            "name": props.get("dealname") or "",
            "deal_type": DealType(dmap["type_values"].get(props.get(dmap["type_property"]), "other")),
            "stage": stage,
            "is_closed": stage in won | lost,
            "is_won": stage in won,
            "amount": parse_decimal_str(props.get("amount")),
            "close_date": parse_date(props.get("closedate")),
            "next_step": props.get(dmap["next_step_property"]) or None,
            "next_step_date": parse_date(props.get(next_step_date_prop)) if next_step_date_prop else None,
            "crm_owner_id": props.get("hubspot_owner_id") or None,
            "created_in_source_at": _created_at(p),
        }
        stmt = insert(Deal).values(row)
        update = {k: stmt.excluded[k] for k in row if k not in {"org_id", "crm_source", "crm_id"}}
        deal_id: uuid.UUID = ctx.session.execute(
            stmt.on_conflict_do_update(
                index_elements=["org_id", "crm_source", "crm_id"], set_=update
            ).returning(Deal.id)
        ).scalar_one()
        if account_id:
            ctx.touched_accounts.add(account_id)
        created = _created_at(p) or _modified_at(p)
        ctx.history(EntityType.deal, deal_id, "exists", [(created, True)], SOURCE)
        deal_type: DealType = row["deal_type"]  # type: ignore[assignment]
        ctx.history(EntityType.deal, deal_id, "deal_type", [(created, deal_type.value)], SOURCE)
        ctx.history(EntityType.deal, deal_id, "stage", _history(p, "dealstage"), SOURCE)
        ctx.history(EntityType.deal, deal_id, "amount", _history(p, "amount", parse_decimal_str), SOURCE)
        ctx.history(EntityType.deal, deal_id, "close_date", _history(p, "closedate", _date_iso), SOURCE)
        ctx.history(
            EntityType.deal,
            deal_id,
            "next_step",
            _history(p, dmap["next_step_property"], lambda v: v or ""),
            SOURCE,
        )
        if next_step_date_prop:
            ctx.history(
                EntityType.deal,
                deal_id,
                "next_step_date",
                _history(p, next_step_date_prop, _date_iso),
                SOURCE,
            )


# ---------------------------------------------------------------- CRM activities


ENGAGEMENT_FIELDS: dict[str, tuple[ActivityType, str | None, str]] = {
    # object → (activity type, subject property, body property)
    "note": (ActivityType.note, None, "hs_note_body"),
    "task": (ActivityType.task, "hs_task_subject", "hs_task_body"),
    "call": (ActivityType.call, "hs_call_title", "hs_call_body"),
}


def engagements(ctx: NormalizeContext, items: list[RawItem]) -> None:
    rows = []
    for item in items:
        p = item.payload
        obj = item.object_type.split(".", 1)[1]
        if obj not in ENGAGEMENT_FIELDS:
            continue
        kind, subject_prop, body_prop = ENGAGEMENT_FIELDS[obj]
        props = _props(p)
        occurred = parse_ts(props.get("hs_timestamp")) or _created_at(p)
        known = parse_ts(props.get("hs_createdate")) or parse_ts(p.get("createdAt")) or occurred
        if occurred is None or known is None:
            continue
        body_html = props.get(body_prop) or ""
        body, _hidden = html_to_text(body_html) if "<" in body_html else (body_html, "")
        account_id = ctx.account_id_for_crm(SOURCE, _first_company(p))
        if account_id:
            ctx.touched_accounts.add(account_id)
        extra: dict[str, Any] = {"crm_object": obj}
        if obj == "task":
            extra["status"] = props.get("hs_task_status")
        rows.append(
            {
                "org_id": ctx.org_id,
                "account_id": account_id,
                "type": kind,
                "direction": Direction.internal,
                "occurred_at": occurred,
                "known_at": known,
                "source": SOURCE,
                "source_id": f"{obj}:{p['id']}",
                "subject": props.get(subject_prop) if subject_prop else None,
                "snippet": snippet(body),
                "body_text": body,
                "crm_owner_id": props.get("hubspot_owner_id"),
                "extra": extra,
                "match_method": MatchMethod.crm_association if account_id else MatchMethod.unmatched,
                "match_confidence": 1.0 if account_id else 0.0,
            }
        )
    if rows:
        stmt = insert(Activity).values(rows)
        keep = {"org_id", "source", "source_id"}
        ctx.session.execute(
            stmt.on_conflict_do_update(
                index_elements=["org_id", "source", "source_id"],
                set_={k: stmt.excluded[k] for k in rows[0] if k not in keep},
            )
        )
