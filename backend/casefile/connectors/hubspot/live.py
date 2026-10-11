"""Read-zone, point-in-time reads from HubSpot for the planner (e.g. a deal's current next step).

Uses the read token only. The planner records what it read as the change's ``before`` value.
"""

from __future__ import annotations

import httpx

from casefile.connectors.http import HttpSource

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
