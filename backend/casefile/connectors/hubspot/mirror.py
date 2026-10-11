"""Sandbox read-back: what changed in the HubSpot sandbox on the deals Casefile mirrored there.

The analysis runs on the synthetic company; the executor writes to a throwaway HubSpot portal where
the open-case renewal deals were copied (``external_ref`` maps each copy to our deal). Without this
reader nothing written there ever comes back, so an executed "next step" never clears the signal it
was meant to clear. This reader closes that loop:

- the mirrored deals, with property history — only changes made *after* the copy was created count
  (the copy's initial values are our own data echoed back);
- the tasks, notes, emails and meetings attached to those deals (what the executor wrote, and
  anything a person logs on them in the portal), re-pointed at our account.

Scope is deliberately narrow. It never lists the portal's companies, contacts or emails: a sandbox
portal connected to someone's real mailbox holds their real, auto-logged mail. Every request is
derived from the mirrored deal ids. Read token only (rule R1).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from casefile.connectors.base import Page, RawItem
from casefile.connectors.http import HttpSource
from casefile.connectors.hubspot.reader import BASE_URL, object_specs
from casefile.ingest.util import field_mapping

DEAL_FIELDS = ["dealname", "dealstage", "amount", "closedate", "hs_next_step", "createdate"]
DEAL_HISTORY = ["dealstage", "amount", "closedate", "hs_next_step"]
ATTACHED = ("tasks", "notes", "emails", "meetings")
BATCH = 100


@dataclass(frozen=True)
class MirroredDeal:
    portal_id: str
    local_deal_crm_id: str  # our deal's crm_id (crm_source "hubspot")
    company_crm_id: str | None  # our account's crm_id, so attached records link to it


class SandboxMirrorReader:
    def __init__(self, token: str, deals: list[MirroredDeal], *, http: HttpSource | None = None) -> None:
        self.http = http or HttpSource(
            "hubspot-sandbox",
            httpx.Client(base_url=BASE_URL, headers={"Authorization": f"Bearer {token}"}, timeout=30),
        )
        self.deals = {d.portal_id: d for d in deals}
        # Same properties the main reader asks for, so the same normalisers apply.
        specs = {s["name"]: s for s in object_specs(field_mapping())}
        self.props = {name: specs[name]["properties"] for name in ATTACHED if name in specs}

    def pages(self, cursor: dict[str, Any]) -> Iterator[Page]:
        # Seven deals and a handful of records: re-read everything each run; the raw store skips
        # payloads it has seen, so only real changes are normalised.
        done = {"last_run": datetime.now(UTC).isoformat(), "deals": len(self.deals)}
        if not self.deals:
            yield Page([], done)
            return
        yield Page(self._deals(), {**cursor, "stage": "deals"})
        for kind in self.props:
            yield Page(self._attached(kind), {**cursor, "stage": kind})
        yield Page([], done)

    def _deals(self) -> list[RawItem]:
        items = []
        ids = list(self.deals)
        for start in range(0, len(ids), BATCH):
            data = self.http.request(
                "POST",
                "/crm/v3/objects/deals/batch/read",
                json={
                    "inputs": [{"id": i} for i in ids[start : start + BATCH]],
                    "properties": DEAL_FIELDS,
                    "propertiesWithHistory": DEAL_HISTORY,
                },
            )
            for deal in data.get("results", []):
                mine = self.deals.get(str(deal["id"]))
                if mine is None:
                    continue
                meta = {"via": "sandbox_mirror", "local_deal_crm_id": mine.local_deal_crm_id}
                items.append(RawItem("sandbox.deal", str(deal["id"]), {**deal, "_casefile": meta}))
        return items

    def _attached(self, kind: str) -> list[RawItem]:
        owner: dict[str, MirroredDeal] = {}
        for portal_id, mine in self.deals.items():
            data = self.http.request(
                "GET", f"/crm/v4/objects/deals/{portal_id}/associations/{kind}", params={"limit": 500}
            )
            for row in data.get("results", []):
                owner.setdefault(str(row["toObjectId"]), mine)
        items = []
        ids = list(owner)
        for start in range(0, len(ids), BATCH):
            data = self.http.request(
                "POST",
                f"/crm/v3/objects/{kind}/batch/read",
                json={
                    "inputs": [{"id": i} for i in ids[start : start + BATCH]],
                    "properties": self.props[kind],
                },
            )
            for obj in data.get("results", []):
                mine = owner[str(obj["id"])]
                companies = [{"id": mine.company_crm_id}] if mine.company_crm_id else []
                payload = {
                    **obj,
                    "_object": kind[:-1],
                    # Point the record at our account (the portal copy has no company, by design).
                    "associations": {"companies": {"results": companies}},
                    "_casefile": {"via": "sandbox_mirror"},
                }
                items.append(RawItem(f"hubspot.{kind[:-1]}", str(obj["id"]), payload))
        return items
