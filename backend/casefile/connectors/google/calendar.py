"""Google Calendar reader (calendar.readonly), primary calendar of one opted-in owner.

Keeps only events with at least one external attendee. Incremental sync uses ``syncToken``; an
expired token (410) restarts a full sync. Cancellations arrive as minimal items and are kept, so
the normaliser can mark the meeting cancelled.

Status: written against Calendar API v3 docs; contract-tested with mocked HTTP. Not yet run live.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from casefile.connectors.base import NotFound, Page, RawItem, ReaderContext
from casefile.connectors.google.auth import AccessToken
from casefile.connectors.google.gmail import BearerAuth
from casefile.connectors.http import HttpSource
from casefile.ingest.util import email_domain

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

    def _keep(self, event: dict[str, Any], internal: set[str]) -> bool:
        if event.get("status") == "cancelled" and "attendees" not in event:
            return True
        return any(email_domain(a.get("email", "")) not in internal for a in event.get("attendees") or [])

    def pages(self, cursor: dict[str, Any]) -> Iterator[Page]:
        calendar = self.http.request("GET", "/calendars/primary")["id"].lower()
        internal = self.context.internal_domains()
        state: dict[str, Any] = dict(cursor)
        try:
            yield from self._run(state, calendar, internal)
        except NotFound:
            state = {}
            yield from self._run(state, calendar, internal)

    def _run(self, state: dict[str, Any], calendar: str, internal: set[str]) -> Iterator[Page]:
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
                if self._keep(e, internal)
            ]
            page_token = data.get("nextPageToken")
            state["page_token"] = page_token
            if not page_token and data.get("nextSyncToken"):
                state["sync_token"] = data["nextSyncToken"]
            yield Page(items, dict(state))
            if not page_token:
                return
