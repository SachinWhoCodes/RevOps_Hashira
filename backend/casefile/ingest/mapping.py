"""Per-organisation field mapping (C2): which CRM properties mean what, for this company.

The YAML file (`field_mapping.yaml`) holds the defaults. An admin can save a company's own mapping;
each save is a new `config_version` row (kind `field_mapping`), never an edit, and is audited. The
effective mapping is the defaults with the latest saved version laid over them.

Validation is strict because a wrong mapping silently changes every score: property names must look
like HubSpot internal names, and every value map may only point at the meanings Casefile knows
(a role is champion / economic_buyer / user / unknown, and so on).

Changing the mapping does not rewrite stored data by itself: `reapply` re-runs the normaliser over
the raw records already stored, so history is rebuilt with the new meanings (no re-fetch needed).
"""

from __future__ import annotations

import copy
import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.audit.log import append as audit
from casefile.db.models import ConfigVersion
from casefile.ingest.util import field_mapping

KIND = "field_mapping"
PROPERTY = re.compile(r"^[a-z0-9_]{1,100}$")
ROLES = {"champion", "economic_buyer", "user", "unknown"}
STATUSES = {"active", "left"}
DEAL_TYPES = {"renewal", "new", "expansion", "other"}
ENGAGEMENTS = {"notes", "tasks", "calls", "emails", "meetings"}


class MappingError(ValueError):
    pass


def defaults() -> dict[str, Any]:
    return copy.deepcopy(field_mapping())


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict) and not key.endswith("_values"):
            _merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)  # value maps and lists are replaced whole
    return base


def latest(session: Session, org_id: uuid.UUID) -> ConfigVersion | None:
    return session.scalar(
        select(ConfigVersion)
        .where(ConfigVersion.org_id == org_id, ConfigVersion.kind == KIND)
        .order_by(ConfigVersion.version.desc())
        .limit(1)
    )


def org_mapping(session: Session | None, org_id: uuid.UUID | None) -> dict[str, Any]:
    """The effective mapping for one organisation (defaults if it never saved its own)."""
    base = defaults()
    if session is None or org_id is None:
        return base
    row = latest(session, org_id)
    return _merge(base, row.body) if row else base


# ---------------------------------------------------------------- validation


def _prop(value: Any, where: str, *, optional: bool = False) -> str | None:
    if value in (None, "") and optional:
        return None
    if not isinstance(value, str) or not PROPERTY.match(value):
        raise MappingError(f"{where}: '{value}' is not a CRM property name (lowercase letters, digits, _)")
    return value


def _values(value: Any, where: str, allowed: set[str]) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise MappingError(f"{where}: needs at least one CRM value mapped")
    out = {}
    for raw, meaning in value.items():
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 100:
            raise MappingError(f"{where}: a CRM value is empty or too long")
        if meaning not in allowed:
            raise MappingError(f"{where}: '{raw}' maps to '{meaning}'; allowed: {', '.join(sorted(allowed))}")
        out[raw.strip()] = meaning
    return out


def _stages(value: Any, where: str, *, required: bool) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise MappingError(f"{where}: a list of deal stage ids")
    if required and not value:
        raise MappingError(f"{where}: at least one stage")
    return [v.strip() for v in value]


def validate(body: dict[str, Any]) -> dict[str, Any]:
    """Check a full mapping as the screen sends it; return the cleaned version to store."""
    hub = (body or {}).get("hubspot")
    if not isinstance(hub, dict):
        raise MappingError("expected a 'hubspot' section")
    company, contact, deal = hub.get("company") or {}, hub.get("contact") or {}, hub.get("deal") or {}
    # A mapping with no champion value is allowed — many CRMs never record buying roles; the
    # champion fallback (signals) then infers a likely champion from email and meeting traffic.
    roles = _values(contact.get("role_values"), "contact roles", ROLES)
    won = _stages(deal.get("won_stages"), "won stages", required=True)
    lost = _stages(deal.get("lost_stages"), "lost stages", required=True)
    if set(won) & set(lost):
        raise MappingError("a stage cannot be both won and lost")
    types = _values(deal.get("type_values"), "deal types", DEAL_TYPES)
    if "renewal" not in types.values():
        raise MappingError("deal types: map at least one CRM deal type to 'renewal'")
    engagement = hub.get("engagement_types")
    if not isinstance(engagement, list) or not set(engagement) <= ENGAGEMENTS:
        raise MappingError(f"engagement types: choose from {', '.join(sorted(ENGAGEMENTS))}")
    return {
        "hubspot": {
            "company": {
                "seats_purchased": _prop(company.get("seats_purchased"), "seats purchased", optional=True),
                "renewal_date": _prop(company.get("renewal_date"), "renewal date", optional=True),
                "arr": _prop(company.get("arr"), "ARR", optional=True),
            },
            "contact": {
                "role_property": _prop(contact.get("role_property"), "contact role property"),
                "role_values": roles,
                "status_property": _prop(contact.get("status_property"), "contact status property"),
                "status_values": _values(contact.get("status_values"), "contact statuses", STATUSES),
            },
            "deal": {
                "type_property": _prop(deal.get("type_property"), "deal type property"),
                "type_values": types,
                "won_stages": won,
                "lost_stages": lost,
                "next_step_property": _prop(deal.get("next_step_property"), "next step property"),
                "next_step_date_property": _prop(
                    deal.get("next_step_date_property"), "next step date property", optional=True
                ),
            },
            "engagement_types": [
                e for e in ("notes", "tasks", "calls", "emails", "meetings") if e in engagement
            ],
        }
    }


def save(session: Session, org_id: uuid.UUID, body: dict[str, Any], user_id: uuid.UUID | None) -> int:
    clean = validate(body)
    current = org_mapping(session, org_id)
    if clean["hubspot"] == current["hubspot"]:
        row = latest(session, org_id)
        return row.version if row else 0  # nothing changed: no new version
    version = (
        session.scalar(
            select(func.max(ConfigVersion.version)).where(
                ConfigVersion.org_id == org_id, ConfigVersion.kind == KIND
            )
        )
        or 0
    ) + 1
    session.add(
        ConfigVersion(org_id=org_id, kind=KIND, version=version, body=clean, created_by_user_id=user_id)
    )
    changed = sorted(
        f"{section}.{key}"
        for section in ("company", "contact", "deal")
        for key in clean["hubspot"][section]
        if clean["hubspot"][section][key] != current["hubspot"][section].get(key)
    )
    if clean["hubspot"]["engagement_types"] != current["hubspot"]["engagement_types"]:
        changed.append("engagement_types")
    audit(
        session,
        org_id,
        "config_changed",
        actor_type="user" if user_id else "system",
        actor_id=str(user_id) if user_id else None,
        entity_type="config",
        entity_id=f"{KIND}:v{version}",
        details={"kind": KIND, "version": version, "changed": changed},
    )
    session.flush()
    return version


# ---------------------------------------------------------------- re-applying to stored data


def reapply(session: Session, org_id: uuid.UUID, user_id: uuid.UUID | None = None) -> dict[str, int]:
    """Re-normalise every stored raw record with the current mapping (no re-fetch), then re-match
    identities and rescore. Derived history at the same timestamps is updated in place."""
    from casefile.cases.service import recompute_all
    from casefile.connectors.base import RawItem
    from casefile.db.models import Connection, Org, RawRecord, Zone
    from casefile.identity.resolver import relink_crm_refs, resolve_identities
    from casefile.ingest.context import NormalizeContext
    from casefile.ingest.normalize import normalize

    org = session.get(Org, org_id)
    assert org is not None
    records = 0
    for conn in session.scalars(
        select(Connection).where(Connection.org_id == org_id, Connection.zone == Zone.read)
    ):
        ctx = NormalizeContext(session=session, org=org, reapply=True)
        last = 0
        while True:
            rows = session.execute(
                select(RawRecord.id, RawRecord.object_type, RawRecord.external_id, RawRecord.payload)
                .where(RawRecord.connection_id == conn.id, RawRecord.id > last)
                .order_by(RawRecord.id)
                .limit(2000)
            ).all()
            if not rows:
                break
            normalize(ctx, [RawItem(r.object_type, r.external_id, r.payload) for r in rows])
            records += len(rows)
            last = rows[-1].id
            session.flush()
    relink_crm_refs(session, org)
    resolve_identities(session, org)
    totals = recompute_all(session, org)
    row = latest(session, org_id)
    audit(
        session,
        org_id,
        "mapping_reapplied",
        actor_type="user" if user_id else "system",
        actor_id=str(user_id) if user_id else None,
        entity_type="config",
        entity_id=f"{KIND}:v{row.version if row else 0}",
        details={"raw_records": records, **{k: v for k, v in totals.items() if isinstance(v, int)}},
    )
    session.flush()
    return {"raw_records": records, **totals}
