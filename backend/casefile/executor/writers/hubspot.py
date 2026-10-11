"""HubSpot writer: tasks, notes and the deal next step (CRM v3 API, private-app token).

Exactly-once: every task and note body ends with the item's marker (``cf<hex>``), a single token
that ``CONTAINS_TOKEN`` search can find, so a crash after the call can be reconciled without
creating a duplicate. The next-step update is naturally idempotent (setting the same value twice
changes nothing), and the executor compares the live value with what the person approved first.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx

from casefile.executor.writers.base import ItemView, WriteRejected, WriteResult, read, send_once

BASE = "https://api.hubapi.com"
# HubSpot-defined association type IDs (v4 associations).
ASSOC = {
    ("tasks", "company"): 192,
    ("tasks", "deal"): 216,
    ("notes", "company"): 190,
    ("notes", "deal"): 214,
}
BODY_PROPERTY = {"tasks": "hs_task_body", "notes": "hs_note_body"}


def _associations(kind: str, ref: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for target in ("company", "deal"):
        if ref.get(target):
            out.append(
                {
                    "to": {"id": str(ref[target])},
                    "types": [
                        {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": ASSOC[(kind, target)]}
                    ],
                }
            )
    return out


def _with_marker(body: str, marker: str) -> str:
    return f'{body}<p style="color:#888">Casefile ref {marker}</p>'


class HubSpotWriter:
    system = "hubspot"

    def __init__(
        self,
        token: str,
        *,
        read_token: str | None = None,
        client: httpx.Client | None = None,
        read_client: httpx.Client | None = None,
    ) -> None:
        self.client = client or httpx.Client(
            base_url=BASE, timeout=30, headers={"Authorization": f"Bearer {token}"}
        )
        # Least privilege: the write app may hold write scopes only, so checks and marker searches
        # go through the read-zone token when one is given.
        if read_client is None and read_token:
            read_client = httpx.Client(
                base_url=BASE, timeout=30, headers={"Authorization": f"Bearer {read_token}"}
            )
        self.read_client = read_client or self.client

    # ---------------------------------------------------------------- writes

    def write(self, item: ItemView) -> WriteResult:
        if item.action_type == "crm_task":
            return self._create_task(item)
        if item.action_type == "crm_note":
            return self._create_note(item)
        if item.action_type == "crm_next_step":
            return self._set_next_step(item)
        raise WriteRejected(f"hubspot writer cannot perform {item.action_type}")

    def _create_task(self, item: ItemView) -> WriteResult:
        p, ref = item.payload, item.target_ref
        due = datetime.combine(date.fromisoformat(p["due_date"]), datetime.min.time(), UTC).replace(hour=12)
        props = {
            "hs_task_subject": p["subject"],
            "hs_task_body": _with_marker(p["body"], item.marker),
            "hs_timestamp": due.isoformat().replace("+00:00", "Z"),
            "hs_task_status": "NOT_STARTED",
            "hs_task_priority": p.get("priority", "MEDIUM"),
            "hs_task_type": "TODO",
        }
        if ref.get("owner"):
            props["hubspot_owner_id"] = str(ref["owner"])
        body = {"properties": props, "associations": _associations("tasks", ref)}
        data = send_once(self.client, "POST", "/crm/v3/objects/tasks", "hubspot task", json=body).json()
        return WriteResult(
            str(data["id"]), {"object": "task", "company": ref.get("company"), "deal": ref.get("deal")}
        )

    def _create_note(self, item: ItemView) -> WriteResult:
        ref = item.target_ref
        props = {
            "hs_note_body": _with_marker(item.payload["body"], item.marker),
            "hs_timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        if ref.get("owner"):
            props["hubspot_owner_id"] = str(ref["owner"])
        body = {"properties": props, "associations": _associations("notes", ref)}
        data = send_once(self.client, "POST", "/crm/v3/objects/notes", "hubspot note", json=body).json()
        return WriteResult(
            str(data["id"]), {"object": "note", "company": ref.get("company"), "deal": ref.get("deal")}
        )

    def _set_next_step(self, item: ItemView) -> WriteResult:
        deal = item.target_ref["deal"]
        prop = item.target_ref.get("property", "hs_next_step")
        send_once(
            self.client,
            "PATCH",
            f"/crm/v3/objects/deals/{deal}",
            "hubspot deal",
            json={"properties": {prop: item.payload["value"]}},
        )
        return WriteResult(str(deal), {"object": "deal", "property": prop, "value": item.payload["value"]})

    # ---------------------------------------------------------------- reads

    def current(self, item: ItemView) -> dict[str, Any]:
        prop = item.target_ref.get("property", "hs_next_step")
        data = read(
            self.read_client,
            "GET",
            f"/crm/v3/objects/deals/{item.target_ref['deal']}",
            "hubspot deal",
            params={"properties": prop},
        )
        return {prop: (data.get("properties") or {}).get(prop) or None}

    def find(self, item: ItemView) -> WriteResult | None:
        if item.action_type == "crm_next_step":
            prop = item.target_ref.get("property", "hs_next_step")
            if self.current(item).get(prop) == item.payload["value"]:
                return WriteResult(str(item.target_ref["deal"]), {"object": "deal", "found_by": "value"})
            return None
        kind = "tasks" if item.action_type == "crm_task" else "notes"
        data = read(
            self.read_client,
            "POST",
            f"/crm/v3/objects/{kind}/search",
            f"hubspot {kind} search",
            json={
                "filterGroups": [
                    {
                        "filters": [
                            {
                                "propertyName": BODY_PROPERTY[kind],
                                "operator": "CONTAINS_TOKEN",
                                "value": item.marker,
                            }
                        ]
                    }
                ],
                "properties": [BODY_PROPERTY[kind]],
                "limit": 5,
            },
        )
        hits = [
            r
            for r in data.get("results", [])
            if item.marker in ((r.get("properties") or {}).get(BODY_PROPERTY[kind]) or "")
        ]
        if not hits:
            return None
        return WriteResult(
            str(hits[0]["id"]), {"object": kind[:-1], "found_by": "marker", "matches": len(hits)}
        )
