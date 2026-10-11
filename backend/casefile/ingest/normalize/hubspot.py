"""HubSpot CRM v3 objects → accounts, contacts, deals, CRM activities and field history.

History comes from ``propertiesWithHistory``. When a property arrives without history (for
example from a list call that didn't request it), its current value is recorded as of the
object's last-modified time, so the as-of loader still has a value — never a future one.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
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
from casefile.ingest.normalize.gmail import BODY_RETENTION
from casefile.ingest.text import html_to_text, snippet, strip_zero_width
from casefile.ingest.util import email_domain, is_system_address, parse_date, parse_decimal_str, parse_ts

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


def _via(p: dict[str, Any]) -> dict[str, Any]:
    """Records read back from the sandbox portal say so on the timeline."""
    via = (p.get("_casefile") or {}).get("via")
    return {"via": via} if via else {}


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
        extra: dict[str, Any] = {"crm_object": obj, **_via(p)}
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


# ---------------------------------------------------------------- logged emails and meetings
#
# HubSpot's email/calendar integrations (or a person) log customer emails and meetings on the CRM
# record. They become the same activity rows Gmail and Calendar produce, so every signal works from
# a CRM token alone. When the mailbox is connected too, the snapshot loader keeps one copy.

# HubSpot links a logged email or meeting to a company automatically (by the contact's company), so
# the match is as good as our own domain rule, not as certain as a person filing a note.
AUTO_ASSOCIATION_CONFIDENCE = 0.95
# Meetings that did not take place. A no-show is not a held meeting: counting it would hide a gap.
NOT_HELD = {"CANCELED", "NO_SHOW"}


def _split_emails(raw: Any) -> list[str]:
    return [e.strip().lower() for e in re.split(r"[;,\s]+", str(raw or "")) if "@" in e]


def _header_emails(headers: dict[str, Any], key: str) -> list[str]:
    value = headers.get(key)
    entries = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    return [str(e["email"]).lower() for e in entries if isinstance(e, dict) and e.get("email")]


def _associated_contacts(ctx: NormalizeContext, p: dict[str, Any]) -> list[tuple[uuid.UUID, str]]:
    results = ((p.get("associations") or {}).get("contacts") or {}).get("results") or []
    found = (ctx.contact_for_crm(SOURCE, str(r["id"])) for r in results)
    return [c for c in found if c is not None]


def _match(
    ctx: NormalizeContext, p: dict[str, Any], participants: list[str]
) -> tuple[uuid.UUID | None, MatchMethod, float]:
    if participants and all(email_domain(e) in ctx.internal_domains for e in participants):
        return None, MatchMethod.internal_only, 0.0
    account_id = ctx.account_id_for_crm(SOURCE, _first_company(p))
    if account_id:
        ctx.touched_accounts.add(account_id)
        return account_id, MatchMethod.crm_association, AUTO_ASSOCIATION_CONFIDENCE
    return None, MatchMethod.unmatched, 0.0  # the identity resolver matches it by email address


def parse_email(ctx: NormalizeContext, p: dict[str, Any]) -> dict[str, Any] | None:
    props = _props(p)
    sent_at = parse_ts(props.get("hs_timestamp")) or _created_at(p)
    if sent_at is None:
        return None
    try:
        headers = json.loads(props.get("hs_email_headers") or "{}")
    except (TypeError, ValueError):
        headers = {}
    headers = headers if isinstance(headers, dict) else {}
    sender = (props.get("hs_email_from_email") or next(iter(_header_emails(headers, "from")), "")).lower()
    to = _split_emails(props.get("hs_email_to_email")) or _header_emails(headers, "to")
    cc = _split_emails(props.get("hs_email_cc_email")) or _header_emails(headers, "cc")

    html = props.get("hs_email_html") or ""
    hidden = ""
    if props.get("hs_email_text"):
        body = str(props["hs_email_text"])
    elif html:
        body, hidden = html_to_text(html)
    else:
        body = ""
    body, had_zero_width = strip_zero_width(body)

    def internal(addr: str) -> bool:
        return email_domain(addr) in ctx.internal_domains

    if sender:
        if internal(sender) and to + cc and all(internal(a) for a in to + cc):
            direction = Direction.internal
        else:
            direction = Direction.outbound if internal(sender) else Direction.inbound
    else:
        incoming = props.get("hs_email_direction") == "INCOMING_EMAIL"
        direction = Direction.inbound if incoming else Direction.outbound
    bounced = props.get("hs_email_status") == "BOUNCED"
    participants = list(dict.fromkeys(a for a in [sender, *to, *cc] if a and not is_system_address(a)))
    account_id, method, confidence = _match(ctx, p, participants)
    contacts = _associated_contacts(ctx, p)
    extra: dict[str, Any] = {
        **_via(p),
        "crm_object": "email",
        "hubspot_direction": props.get("hs_email_direction"),
        "status": props.get("hs_email_status"),
        "auto_reply": str(props.get("hs_incoming_email_is_out_of_office") or "").lower() == "true",
        "bounce": bounced,
    }
    if bounced and to:
        extra["failed_recipient"] = to[0]  # HubSpot marks our own sent email as bounced
    if hidden or had_zero_width:
        extra["hidden_text"] = hidden[:2000]
        extra["zero_width_chars"] = had_zero_width
    message_id = (props.get("hs_email_message_id") or "").strip()
    return {
        "type": ActivityType.email,
        "direction": direction,
        "occurred_at": sent_at,
        "known_at": sent_at,  # as for Gmail: the email existed in the mailbox when it was sent
        "cancelled_at": None,
        "source": SOURCE,
        "source_id": f"email:{p['id']}",
        "dedupe_key": message_id or None,
        "thread_id": f"hubspot:{props['hs_email_thread_id']}" if props.get("hs_email_thread_id") else None,
        "subject": props.get("hs_email_subject"),
        "snippet": snippet(body),
        "body_text": body,
        "from_email": sender or None,
        "to_emails": to,
        "cc_emails": cc,
        "participant_emails": participants,
        "contact_ids": [c[0] for c in contacts] if method == MatchMethod.crm_association else [],
        "crm_owner_id": props.get("hubspot_owner_id"),
        "extra": extra,
        "account_id": account_id,
        "match_method": method,
        "match_confidence": confidence,
    }


def parse_meeting(ctx: NormalizeContext, p: dict[str, Any]) -> dict[str, Any] | None:
    props = _props(p)
    start = parse_ts(props.get("hs_meeting_start_time")) or parse_ts(props.get("hs_timestamp"))
    booked = parse_ts(props.get("hs_createdate")) or _created_at(p)
    if start is None or booked is None:
        return None
    organizer = (props.get("hs_organizer_email") or "").strip().lower()
    contacts = _associated_contacts(ctx, p)
    attendees = [
        *_split_emails(props.get("hs_all_attendee_emails")),
        *_split_emails(props.get("hs_guest_emails")),
        *(email for _, email in contacts),
    ]
    participants = [e for e in dict.fromkeys([organizer, *attendees]) if e and not is_system_address(e)]
    account_id, method, confidence = _match(ctx, p, participants)
    if method == MatchMethod.internal_only or (not participants and account_id is None):
        return None  # an internal meeting, as Calendar does
    outcome = (props.get("hs_meeting_outcome") or "").upper() or None
    end = parse_ts(props.get("hs_meeting_end_time"))
    body = str(props.get("hs_meeting_body") or "")
    if "<" in body:
        body, _hidden = html_to_text(body)
    return {
        "type": ActivityType.meeting,
        "direction": (
            Direction.inbound
            if organizer and email_domain(organizer) not in ctx.internal_domains
            else Direction.outbound
        ),
        "occurred_at": start,
        "known_at": booked,  # a meeting booked in March for May counts as booked in April
        "cancelled_at": _modified_at(p) if outcome in NOT_HELD else None,
        "source": SOURCE,
        "source_id": f"meeting:{p['id']}",
        "dedupe_key": None,
        "thread_id": None,
        "subject": props.get("hs_meeting_title"),
        "snippet": snippet(body),
        "body_text": None,  # meeting notes are not needed by any signal; keep the snippet only
        "from_email": organizer or None,
        "to_emails": [],
        "cc_emails": [],
        "participant_emails": participants,
        "contact_ids": [c[0] for c in contacts] if method == MatchMethod.crm_association else [],
        "crm_owner_id": props.get("hubspot_owner_id"),
        "extra": {
            **_via(p),
            "crm_object": "meeting",
            "outcome": outcome,
            "end": end.isoformat() if end else None,
            "meeting_source": props.get("hs_meeting_source"),
        },
        "account_id": account_id,
        "match_method": method,
        "match_confidence": confidence,
    }


def _upsert_logged(ctx: NormalizeContext, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    stmt = insert(Activity).values(rows)
    keep = {"org_id", "source", "source_id"}
    # Logged emails and meetings change (bounce status, outcome, reschedule): latest wins. A match a
    # person made by hand (manual override) is kept by the resolver, which runs after this.
    ctx.session.execute(
        stmt.on_conflict_do_update(
            index_elements=["org_id", "source", "source_id"],
            set_={k: stmt.excluded[k] for k in rows[0] if k not in keep},
        )
    )


def logged_emails(ctx: NormalizeContext, items: list[RawItem]) -> None:
    purge_after = ctx.now() + BODY_RETENTION
    rows = []
    for item in items:
        parsed = parse_email(ctx, item.payload)
        if parsed:
            rows.append({**parsed, "org_id": ctx.org_id, "body_purge_after": purge_after})
    _upsert_logged(ctx, rows)


def logged_meetings(ctx: NormalizeContext, items: list[RawItem]) -> None:
    by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        parsed = parse_meeting(ctx, item.payload)
        if parsed:
            by_id[parsed["source_id"]] = {**parsed, "org_id": ctx.org_id, "body_purge_after": None}
    _upsert_logged(ctx, list(by_id.values()))


# ---------------------------------------------------------------- sandbox read-back (deals)

SANDBOX_SOURCE = "hubspot_sandbox"
# A copy's own creation is our data echoed back; only later changes are news.
COPY_SETTLE = timedelta(seconds=2)


def _recorded_history(p: dict[str, Any], prop: str, convert: Any = lambda v: v) -> list[tuple[datetime, Any]]:
    """Only what HubSpot recorded as history — never the current-value fallback, which would stamp
    an unchanged value with a new time and reset how long a next step has gone untouched."""
    out = []
    for entry in (p.get("propertiesWithHistory") or {}).get(prop) or []:
        at = parse_ts(entry.get("timestamp"))
        if at is not None:
            out.append((at, convert(entry.get("value"))))
    return out


def sandbox_deals(ctx: NormalizeContext, items: list[RawItem]) -> None:
    """Changes made in the sandbox portal to a mirrored deal become history on our deal."""
    for item in items:
        p = item.payload
        local_crm_id = (p.get("_casefile") or {}).get("local_deal_crm_id")
        deal = ctx.session.scalar(
            select(Deal).where(
                Deal.org_id == ctx.org_id, Deal.crm_source == SOURCE, Deal.crm_id == local_crm_id
            )
        )
        copied_at = parse_ts(p.get("createdAt")) or _created_at(p)
        if deal is None or copied_at is None:
            continue
        changes = {
            "next_step": _recorded_history(p, "hs_next_step", lambda v: v or ""),
            "stage": _recorded_history(p, "dealstage"),
            "amount": _recorded_history(p, "amount", parse_decimal_str),
            "close_date": _recorded_history(p, "closedate", _date_iso),
        }
        touched = False
        for field_name, entries in changes.items():
            later = [(at, v) for at, v in entries if at > copied_at + COPY_SETTLE]
            if not later:
                continue
            touched = True
            ctx.history(EntityType.deal, deal.id, field_name, later, SANDBOX_SOURCE)
            latest = max(later, key=lambda e: e[0])[1]
            if field_name == "next_step":
                deal.next_step = latest or None
            elif field_name == "stage":
                dmap = ctx.mapping["hubspot"]["deal"]
                won, lost = set(dmap["won_stages"]), set(dmap["lost_stages"])
                deal.stage, deal.is_closed, deal.is_won = latest, latest in won | lost, latest in won
            elif field_name == "amount":
                deal.amount = latest
            elif field_name == "close_date":
                deal.close_date = parse_date(latest)
        if touched and deal.account_id:
            ctx.touched_accounts.add(deal.account_id)
