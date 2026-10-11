"""Field mapping screen (C2): which CRM properties mean what, for this company. Admin only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from casefile.api.deps import get_db, require_admin
from casefile.connectors.base import AuthError
from casefile.connectors.hubspot.live import read_connection
from casefile.connectors.registry import connection_secret
from casefile.db.models import AppUser, Connection
from casefile.ingest.mapping import MappingError, defaults, latest, org_mapping, save, validate
from casefile.worker.jobs import enqueue

router = APIRouter(prefix="/admin/field-mapping", tags=["mapping"])


def _live_crm(db: Session, admin: AppUser) -> Connection | None:
    """A real HubSpot read connection (never the sandbox read-back, whose portal holds personal mail)."""
    return read_connection(db, admin.org_id, include_mirror=False)


@router.get("")
def get_mapping(db: Session = Depends(get_db), admin: AppUser = Depends(require_admin)) -> dict[str, Any]:
    row = latest(db, admin.org_id)
    return {
        "mapping": org_mapping(db, admin.org_id),
        "defaults": defaults(),
        "version": row.version if row else 0,
        "saved_at": row.created_at.isoformat() if row else None,
        "live_crm": _live_crm(db, admin) is not None,
    }


@router.put("")
def put_mapping(
    body: dict[str, Any], db: Session = Depends(get_db), admin: AppUser = Depends(require_admin)
) -> dict[str, Any]:
    try:
        version = save(db, admin.org_id, body, admin.id)
    except MappingError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return {"version": version, "mapping": org_mapping(db, admin.org_id), "reapply_needed": True}


@router.post("/check")
def check_mapping(
    body: dict[str, Any], db: Session = Depends(get_db), admin: AppUser = Depends(require_admin)
) -> dict[str, Any]:
    """Validate, and — when a real CRM is connected — compare every property and stage with its schema."""
    try:
        clean = validate(body)["hubspot"]
    except MappingError as exc:
        return {"ok": False, "problems": [str(exc)], "checked_against_crm": False}
    conn = _live_crm(db, admin)
    if conn is None:
        return {
            "ok": True,
            "problems": [],
            "checked_against_crm": False,
            "note": "No live CRM is connected, so property names were not checked against it.",
        }
    from casefile.connectors.hubspot.live import HubSpotLive

    try:
        token = connection_secret(conn)
    except AuthError:
        return {"ok": True, "problems": [], "checked_against_crm": False, "note": "CRM token not available"}
    schema = HubSpotLive(token).schema_details()
    problems = []
    wanted = [
        ("companies", clean["company"]["seats_purchased"]),
        ("companies", clean["company"]["renewal_date"]),
        ("companies", clean["company"]["arr"]),
        ("contacts", clean["contact"]["role_property"]),
        ("contacts", clean["contact"]["status_property"]),
        ("deals", clean["deal"]["type_property"]),
        ("deals", clean["deal"]["next_step_property"]),
        ("deals", clean["deal"]["next_step_date_property"]),
    ]
    for obj, prop in wanted:
        if prop and prop not in schema[obj]:
            problems.append(f"{obj}: no property named '{prop}' in your CRM")
    for stage in clean["deal"]["won_stages"] + clean["deal"]["lost_stages"]:
        if stage not in schema["deal_stages"]:
            problems.append(f"deal stage '{stage}' does not exist in any of your pipelines")
    return {
        "ok": not problems,
        "problems": problems,
        "checked_against_crm": True,
        "suggestions": suggest(clean, schema),
    }


# What each mapped field usually looks like in a real CRM: (object, words in the name or label, types).
SUGGEST_RULES: dict[str, tuple[str, tuple[str, ...], set[str]]] = {
    "company.renewal_date": (
        "companies",
        ("renewal", "contract end", "contract_end", "end date", "expir", "term end"),
        {"date", "datetime"},
    ),
    "company.seats_purchased": ("companies", ("seat", "licen", "user count", "users purchased"), {"number"}),
    "company.arr": (
        "companies",
        ("arr", "annual recurring", "recurring revenue", "contract value", "acv", "mrr"),
        {"number"},
    ),
    "contact.role_property": ("contacts", ("buying role", "buying_role", "persona", "role"), {"enumeration"}),
    "contact.status_property": (
        "contacts",
        ("employment", "left company", "still at"),
        {"enumeration", "bool"},
    ),
}


def suggest(clean: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Likely matches in *this* CRM for fields whose mapped property is missing, and won/lost stages
    taken from each pipeline's own closed stages (probability 1 = won, 0 = lost). Suggestions only:
    nothing is saved until an admin picks one."""
    out: dict[str, Any] = {}
    for key, (obj, words, types) in SUGGEST_RULES.items():
        section, field = key.split(".")
        current = clean[section].get(field)
        if not current or current in schema[obj]:
            continue
        scored = []
        for p in schema[f"{obj}_details"]:
            text = f"{p['name']} {p['label']}".lower().replace("_", " ")
            hits = sum(w.replace("_", " ") in text for w in words)
            if hits and (not types or p["type"] in types):
                scored.append((-hits, p["name"].startswith("hs_"), p["name"], p["label"]))
        if scored:
            out[key] = [{"name": n, "label": label} for _, _, n, label in sorted(scored)[:5]]
    stages = schema.get("deal_stages_details") or []
    won = [s for s in stages if s["closed"] and (s["probability"] or 0) >= 1]
    lost = [s for s in stages if s["closed"] and s["probability"] == 0]
    known = set(schema["deal_stages"])
    if any(st not in known for st in clean["deal"]["won_stages"]) and won:
        out["deal.won_stages"] = [{"name": s["id"], "label": f"{s['pipeline']}: {s['label']}"} for s in won]
    if any(st not in known for st in clean["deal"]["lost_stages"]) and lost:
        out["deal.lost_stages"] = [{"name": s["id"], "label": f"{s['pipeline']}: {s['label']}"} for s in lost]
    return out


@router.post("/reapply")
def reapply_mapping(db: Session = Depends(get_db), admin: AppUser = Depends(require_admin)) -> dict[str, Any]:
    """Rebuild stored data with the current mapping. Runs on the worker (it can take minutes)."""
    enqueue(
        db,
        "reapply_mapping",
        {"user_id": str(admin.id)},
        org_id=admin.org_id,
        dedupe_key=f"reapply_mapping:{admin.org_id}",
    )
    db.commit()
    return {"queued": True}
