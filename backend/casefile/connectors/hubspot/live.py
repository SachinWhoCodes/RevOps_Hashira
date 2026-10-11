"""Read-zone, point-in-time reads from HubSpot for the planner (e.g. a deal's current next step).

Uses the read token only. The planner records what it read as the change's ``before`` value.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.connectors.base import AuthError
from casefile.connectors.http import HttpSource
from casefile.db.models import Connection, ConnectionKind, ConnectionStatus, Zone

BASE = "https://api.hubapi.com"


class HubSpotLive:
    def __init__(self, token: str, client: httpx.Client | None = None) -> None:
        http = client or httpx.Client(base_url=BASE, timeout=20, headers={"Authorization": f"Bearer {token}"})
        self.source = HttpSource("hubspot live", http)

    def deal_next_step(self, deal_id: str) -> str | None:
        data = self.source.request(
            "GET", f"/crm/v3/objects/deals/{deal_id}", params={"properties": "hs_next_step"}
        )
        return (data.get("properties") or {}).get("hs_next_step") or None

    def schema(self) -> dict[str, list[str]]:
        """Property names per object, and deal stage ids across pipelines: the CRM's *schema*, used
        to check a field mapping. Reads no records."""
        return {k: v for k, v in self.schema_details().items() if not k.endswith("_details")}

    def schema_details(self) -> dict[str, Any]:
        """Like ``schema`` plus each property's label and type, and each deal stage's pipeline, label,
        closed flag and probability — what is needed to *suggest* a mapping. Still no records."""
        out: dict[str, Any] = {}
        for obj in ("companies", "contacts", "deals"):
            data = self.source.request("GET", f"/crm/v3/properties/{obj}")
            results = data.get("results", [])
            out[obj] = sorted(p["name"] for p in results)
            out[f"{obj}_details"] = [
                {"name": p["name"], "label": p.get("label") or "", "type": p.get("type") or ""}
                for p in results
            ]
        pipelines = self.source.request("GET", "/crm/v3/pipelines/deals")
        stages = []
        for pl in pipelines.get("results", []):
            for st in pl.get("stages", []):
                meta = st.get("metadata") or {}
                stages.append(
                    {
                        "id": st["id"],
                        "label": st.get("label") or "",
                        "pipeline": pl.get("label") or "",
                        "closed": str(meta.get("isClosed", "")).lower() == "true",
                        "probability": float(meta["probability"])
                        if meta.get("probability") not in (None, "")
                        else None,
                    }
                )
        out["deal_stages"] = sorted({s["id"] for s in stages})
        out["deal_stages_details"] = stages
        return out

    def account(self) -> dict[str, object]:
        """Which portal this token belongs to (no records read)."""
        data = self.source.request("GET", "/account-info/v3/details")
        return {
            "portal_id": str(data.get("portalId")),
            "time_zone": data.get("timeZone"),
            "currency": data.get("companyCurrency"),
            "ui_domain": data.get("uiDomain"),
        }

    def can_write(self) -> dict[str, str]:
        return can_write(self.source)

    def can_read(self, objects: list[str]) -> dict[str, str]:
        """For each object type, "ok" or "refused" — by asking for one record id (nothing is kept).

        HubSpot has no scope-introspection endpoint for static app tokens, so this is the honest check.
        """
        out: dict[str, str] = {}
        for name in objects:
            path = "/crm/v3/owners" if name == "owners" else f"/crm/v3/objects/{name}"
            params = {"limit": 1} if name == "owners" else {"limit": 1, "properties": "hs_object_id"}
            try:
                self.source.request("GET", path, params=params)
                out[name] = "ok"
            except AuthError as exc:
                if exc.status != 403:
                    raise
                out[name] = "refused"
        return out


# What Casefile writes (casefile.executor.writers.hubspot): tasks and notes, and the deal next step.
WRITE_PROBES = {
    "tasks": "/crm/v3/objects/tasks/batch/create",
    "notes": "/crm/v3/objects/notes/batch/create",
    "deals": "/crm/v3/objects/deals/batch/update",
}


def can_write(source: HttpSource) -> dict[str, str]:
    """For each kind of write, "ok" or "refused" — by sending an *empty* batch (zero records), which
    HubSpot authorises (200/201, nothing created) or refuses for a missing scope (403). Observed on the
    sandbox portal: the write app gets 200/201, the read app 403."""
    out: dict[str, str] = {}
    for name, path in WRITE_PROBES.items():
        try:
            data = source.request("POST", path, json={"inputs": []})
        except AuthError as exc:
            if exc.status != 403:
                raise
            out[name] = "refused"
            continue
        if data.get("results"):  # never expected: an empty batch must not create anything
            raise RuntimeError(f"HubSpot returned results for an empty {name} batch")
        out[name] = "ok"
    return out


def read_connection(session: Session, org_id: uuid.UUID, *, include_mirror: bool = True) -> Connection | None:
    """This organisation's active HubSpot read connection. A real CRM is preferred over the sandbox
    read-back; the read-back is used only when ``include_mirror`` (for "before" values on its deals)."""
    conns = list(
        session.scalars(
            select(Connection)
            .where(
                Connection.org_id == org_id,
                Connection.kind == ConnectionKind.hubspot,
                Connection.zone == Zone.read,
                Connection.status == ConnectionStatus.active,
            )
            .order_by(Connection.created_at)
        )
    )
    real = [c for c in conns if (c.config or {}).get("mode") != "sandbox_mirror"]
    if real:
        return real[0]
    return conns[0] if conns and include_mirror else None


def live_crm_for(session: Session, org_id: uuid.UUID) -> HubSpotLive | None:
    """Point-in-time HubSpot reads with *this organisation's* read credential (None when it has none)."""
    from casefile.connectors.registry import connection_secret

    conn = read_connection(session, org_id)
    if conn is None:
        return None
    try:
        return HubSpotLive(connection_secret(conn))
    except AuthError:
        return None
