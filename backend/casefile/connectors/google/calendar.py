"""Google Calendar reader (calendar.readonly), primary calendar of one opted-in owner.

Keeps only events with a customer attendee (a known contact, or an address at a customer domain) —
not every event with an outsider, which would pull in vendors, candidates and private appointments.
Incremental sync uses ``syncToken``; an expired token (410) restarts a full sync. When the CRM brings
customers the calendar was never checked for, the window is listed again (cheap: 250 events per call)
so their past meetings are kept. Cancellations arrive as minimal items and are kept, so the
normaliser can mark a meeting it already has as cancelled.

Status: contract-tested with mocked HTTP; run live on 2026-10-11 against a real calendar (listing and
sync token) with nothing kept (no customers in the rehearsal organisation).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from casefile.connectors.base import NotFound, Page, RawItem, ReaderContext
from casefile.connectors.google.auth import AccessToken
from casefile.connectors.google.gmail import BearerAuth, matches, target_hashes
from casefile.connectors.http import HttpSource

BASE_URL = "https://www.googleapis.com/calendar/v3"


class CalendarReader:
    def __init__(
        self,
        token: AccessToken,
        context: ReaderContext,
        *,
        window_days: int = 730,
        http: HttpSource | None = None,
    ) -> None:
        self.http = http or HttpSource(
            "gcal", httpx.Client(base_url=BASE_URL, auth=BearerAuth(token), timeout=30)
        )
        self.context = context
        self.window_days = window_days

    def _keep(self, event: dict[str, Any], domains: set[str], known: set[str]) -> bool:
        if event.get("status") == "cancelled" and "attendees" not in event:
            return True
        return any(matches(a.get("email", ""), domains, known) for a in event.get("attendees") or [])

    def pages(self, cursor: dict[str, Any]) -> Iterator[Page]:
        calendar = self.http.request("GET", "/calendars/primary")["id"].lower()
        internal = self.context.internal_domains()
        domains = {d for d in self.context.customer_domains() if d not in internal}
        known = self.context.known_emails()
        targets = target_hashes(domains, known)
        state: dict[str, Any] = dict(cursor)
        if state.get("sync_token") and set(targets) - set(state.get("searched", [])):
            state = {}  # new customers since the last full listing: list the window again
        try:
            yield from self._run(state, calendar, domains, known, targets)
        except NotFound:
            state = {}
            yield from self._run(state, calendar, domains, known, targets)

    def _run(
        self, state: dict[str, Any], calendar: str, domains: set[str], known: set[str], targets: list[str]
    ) -> Iterator[Page]:
        page_token = state.get("page_token")
        while True:
            if state.get("sync_token"):
                params: dict[str, Any] = {"syncToken": state["sync_token"]}
            else:
                time_min = datetime.now(UTC) - timedelta(days=self.window_days)
                params = {"timeMin": time_min.isoformat(), "singleEvents": "true", "showDeleted": "true"}
            params["maxResults"] = 250
            if page_token:
                params["pageToken"] = page_token
            data = self.http.request("GET", "/calendars/primary/events", params=params)
            items = [
                RawItem("gcal.event", f"{calendar}:{e['id']}", {**e, "_casefile": {"calendar": calendar}})
                for e in data.get("items", [])
                if self._keep(e, domains, known)
            ]
            page_token = data.get("nextPageToken")
            state["page_token"] = page_token
            if not page_token and data.get("nextSyncToken"):
                state["sync_token"] = data["nextSyncToken"]
                state["searched"] = targets
            yield Page(items, dict(state))
            if not page_token:
                return
