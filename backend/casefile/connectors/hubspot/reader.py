"""HubSpot CRM v3 reader (read-only token).

First run: list every object with ``propertiesWithHistory`` and company associations.
Later runs: search for objects modified since the last run (with a 5-minute overlap), then fetch
each changed object with its history. Cursors are saved per object type and per page, so an
interrupted sync resumes.

Emails and meetings logged in HubSpot (by its Gmail/Outlook/calendar integrations or by hand) are
read too, with their contact associations, so a company can try Casefile with one CRM token and no
mailbox access. If the token lacks the scope for one of them (HubSpot answers 403), that object is
skipped and noted in the cursor; the rest of the sync carries on. Property names were checked
against a live portal on 2026-10-11.

Status: written against HubSpot's documented v3 endpoints; contract-tested with mocked HTTP.
Not yet run as a full sync against a live portal (spike S2).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from casefile.connectors.base import AuthError, Page, RawItem
from casefile.connectors.http import HttpSource

BASE_URL = "https://api.hubapi.com"
OVERLAP = timedelta(minutes=5)
LIST_LIMIT = 50  # HubSpot caps list pages at 50 when property history is requested
SEARCH_LIMIT = 100
ENGAGEMENT_OBJECTS = {"hubspot.note", "hubspot.task", "hubspot.call", "hubspot.email", "hubspot.meeting"}
WITH_CONTACTS = {"emails", "meetings"}  # who took part comes from the contact associations


def object_specs(mapping: dict[str, Any]) -> list[dict[str, Any]]:
    """What to fetch per object: properties, which of them with history, and the modified field."""
    company, contact, deal = (mapping["hubspot"][k] for k in ("company", "contact", "deal"))
    company_custom = [
        v for v in (company.get("seats_purchased"), company.get("renewal_date"), company.get("arr")) if v
    ]
    deal_custom = [v for v in (deal.get("next_step_date_property"),) if v]
    specs: list[dict[str, Any]] = [
        {
            "name": "companies",
            "object_type": "hubspot.company",
            "modified": "hs_lastmodifieddate",
            "properties": [
                "name",
                "domain",
                "hs_additional_domains",
                "hubspot_owner_id",
                "createdate",
                "hs_lastmodifieddate",
                *company_custom,
            ],
            "history": ["name", "domain", "hubspot_owner_id", *company_custom],
        },
        {
            "name": "contacts",
            "object_type": "hubspot.contact",
            "modified": "lastmodifieddate",
            "properties": [
                "email",
                "firstname",
                "lastname",
                "jobtitle",
                "associatedcompanyid",
                "createdate",
                "lastmodifieddate",
                contact["role_property"],
                contact["status_property"],
            ],
            "history": [
                "jobtitle",
                "associatedcompanyid",
                contact["role_property"],
                contact["status_property"],
            ],
        },
        {
            "name": "deals",
            "object_type": "hubspot.deal",
            "modified": "hs_lastmodifieddate",
            "properties": [
                "dealname",
                "dealtype",
                "pipeline",
                "dealstage",
                "amount",
                "closedate",
                "hubspot_owner_id",
                "createdate",
                "hs_lastmodifieddate",
                deal["next_step_property"],
                *deal_custom,
            ],
            "history": ["dealstage", "amount", "closedate", deal["next_step_property"], *deal_custom],
        },
    ]
    engagement_props = {
        "notes": ["hs_note_body", "hs_timestamp", "hubspot_owner_id", "hs_createdate"],
        "tasks": ["hs_task_subject", "hs_task_body", "hs_task_status", "hs_timestamp", "hubspot_owner_id"],
        "calls": ["hs_call_title", "hs_call_body", "hs_call_direction", "hs_timestamp", "hubspot_owner_id"],
        "emails": [
            "hs_timestamp",
            "hs_createdate",
            "hs_email_direction",
            "hs_email_status",
            "hs_email_subject",
            "hs_email_text",
            "hs_email_html",
            "hs_email_headers",
            "hs_email_from_email",
            "hs_email_to_email",
            "hs_email_cc_email",
            "hs_email_message_id",
            "hs_email_thread_id",
            "hs_incoming_email_is_out_of_office",
            "hubspot_owner_id",
        ],
        "meetings": [
            "hs_timestamp",
            "hs_createdate",
            "hs_meeting_title",
            "hs_meeting_body",
            "hs_meeting_start_time",
            "hs_meeting_end_time",
            "hs_meeting_outcome",
            "hs_all_attendee_emails",
            "hs_guest_emails",
            "hs_organizer_email",
            "hs_meeting_source",
            "hubspot_owner_id",
        ],
    }
    for name in mapping["hubspot"].get("engagement_types", []):
        if name in engagement_props:
            specs.append(
                {
                    "name": name,
                    "object_type": f"hubspot.{name[:-1]}",
                    "modified": "hs_lastmodifieddate",
                    "properties": [*engagement_props[name], "hs_lastmodifieddate"],
                    "history": [],
                    # Reading emails needs an extra HubSpot scope; without it, skip rather than fail.
                    "optional": name in WITH_CONTACTS,
                }
            )
    return specs


class HubSpotReader:
    def __init__(self, token: str, mapping: dict[str, Any], *, http: HttpSource | None = None) -> None:
        self.http = http or HttpSource(
            "hubspot",
            httpx.Client(base_url=BASE_URL, headers={"Authorization": f"Bearer {token}"}, timeout=30),
        )
        self.specs = object_specs(mapping)

    # ------------------------------------------------------------ pages

    def pages(self, cursor: dict[str, Any]) -> Iterator[Page]:
        state = {k: dict(v) for k, v in cursor.items() if isinstance(v, dict)}
        yield Page(list(self._owners()), self._snapshot(state))
        for spec in self.specs:
            obj_state = state.setdefault(spec["name"], {})
            started = datetime.now(UTC)
            try:
                if obj_state.get("since") is None:
                    yield from self._full(spec, obj_state, state)
                else:
                    yield from self._incremental(spec, obj_state, state)
            except AuthError as exc:
                if not (spec.get("optional") and exc.status == 403):
                    raise
                # Missing scope for this object only: note it and carry on; retried next run.
                obj_state.pop("after", None)
                obj_state["skipped"] = "HubSpot refused (403): the token lacks the scope to read these"
                yield Page([], self._snapshot(state))
                continue
            obj_state.pop("skipped", None)
            obj_state.pop("after", None)
            obj_state["since"] = (started - OVERLAP).isoformat()
            yield Page([], self._snapshot(state))

    @staticmethod
    def _snapshot(state: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {k: dict(v) for k, v in state.items()}

    def _owners(self) -> Iterator[RawItem]:
        after: str | None = None
        while True:
            params: dict[str, Any] = {"limit": 100}
            if after:
                params["after"] = after
            data = self.http.request("GET", "/crm/v3/owners", params=params)
            for owner in data.get("results", []):
                yield RawItem("hubspot.owner", str(owner["id"]), owner)
            after = ((data.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return

    def _item(self, spec: dict[str, Any], obj: dict[str, Any]) -> RawItem:
        if spec["object_type"] in ENGAGEMENT_OBJECTS:
            obj = {**obj, "_object": spec["object_type"].split(".")[1]}
        return RawItem(spec["object_type"], str(obj["id"]), obj)

    def _params(self, spec: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {"properties": ",".join(spec["properties"]), "archived": "false"}
        if spec["history"]:
            params["propertiesWithHistory"] = ",".join(spec["history"])
        if spec["name"] in WITH_CONTACTS:
            params["associations"] = "companies,contacts"
        elif spec["name"] != "companies":
            params["associations"] = "companies"
        return params

    def _full(
        self, spec: dict[str, Any], obj_state: dict[str, Any], state: dict[str, dict[str, Any]]
    ) -> Iterator[Page]:
        after = obj_state.get("after")
        while True:
            params = {**self._params(spec), "limit": LIST_LIMIT}
            if after:
                params["after"] = after
            data = self.http.request("GET", f"/crm/v3/objects/{spec['name']}", params=params)
            after = ((data.get("paging") or {}).get("next") or {}).get("after")
            obj_state["after"] = after
            yield Page([self._item(spec, o) for o in data.get("results", [])], self._snapshot(state))
            if not after:
                return

    def _incremental(
        self, spec: dict[str, Any], obj_state: dict[str, Any], state: dict[str, dict[str, Any]]
    ) -> Iterator[Page]:
        since_ms = int(datetime.fromisoformat(obj_state["since"]).timestamp() * 1000)
        after = obj_state.get("after")
        while True:
            body: dict[str, Any] = {
                "filterGroups": [
                    {
                        "filters": [
                            {"propertyName": spec["modified"], "operator": "GTE", "value": str(since_ms)}
                        ]
                    }
                ],
                "sorts": [{"propertyName": spec["modified"], "direction": "ASCENDING"}],
                "properties": ["hs_object_id"],
                "limit": SEARCH_LIMIT,
            }
            if after:
                body["after"] = after
            data = self.http.request("POST", f"/crm/v3/objects/{spec['name']}/search", json=body)
            items = [
                self._item(
                    spec,
                    self.http.request(
                        "GET", f"/crm/v3/objects/{spec['name']}/{hit['id']}", params=self._params(spec)
                    ),
                )
                for hit in data.get("results", [])
            ]
            after = ((data.get("paging") or {}).get("next") or {}).get("after")
            obj_state["after"] = after
            yield Page(items, self._snapshot(state))
            if not after:
                return
