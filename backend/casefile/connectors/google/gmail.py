"""Gmail reader (gmail.readonly), one connection per opted-in mailbox.

Data minimisation: for each candidate message we first fetch headers only, and fetch the full
message only when a participant belongs to a known customer domain or contact, when it continues a
thread we already keep, or when it is a delivery-failure notice. Everything else is never fetched.

First run lists messages in the history window; later runs read ``history.list`` from the saved
history id. An expired history id (404) falls back to a full window scan.

Status: written against Gmail API v1 docs; contract-tested with mocked HTTP. Not yet run against a
live mailbox (spike S1).
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from datetime import UTC, datetime, timedelta
from email.utils import getaddresses
from typing import Any

import httpx

from casefile.connectors.base import NotFound, Page, RawItem, ReaderContext
from casefile.connectors.google.auth import AccessToken
from casefile.connectors.http import HttpSource
from casefile.ingest.util import email_domain, is_system_address

BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_KEPT_THREADS = 5000


class BearerAuth(httpx.Auth):
    def __init__(self, token: AccessToken) -> None:
        self.token = token

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self.token.get()}"
        yield request


class GmailReader:
    def __init__(
        self,
        token: AccessToken,
        context: ReaderContext,
        *,
        window_days: int = 730,
        page_size: int = 100,
        http: HttpSource | None = None,
    ) -> None:
        self.http = http or HttpSource(
            "gmail", httpx.Client(base_url=BASE_URL, auth=BearerAuth(token), timeout=30)
        )
        self.context = context
        self.window_days = window_days
        self.page_size = page_size

    # ------------------------------------------------------------ relevance

    def _relevant(
        self, meta: dict[str, Any], kept_threads: set[str], domains: set[str], known: set[str]
    ) -> bool:
        if meta.get("threadId") in kept_threads:
            return True
        headers = {h["name"].lower(): h["value"] for h in (meta.get("payload") or {}).get("headers", [])}
        addrs = [
            a.lower() for _, a in getaddresses([headers.get(k, "") for k in ("from", "to", "cc")]) if "@" in a
        ]
        if any(is_system_address(a) for a in addrs[:1]) and "deliver" in headers.get("subject", "").lower():
            return True  # bounce notice: needed to detect champion departure
        for a in addrs:
            if a in known:
                return True
            d = email_domain(a)
            while d.count(".") >= 1:
                if d in domains:
                    return True
                d = d.split(".", 1)[1]
        return False

    # ------------------------------------------------------------ pages

    def pages(self, cursor: dict[str, Any]) -> Iterator[Page]:
        profile = self.http.request("GET", "/profile")
        mailbox = profile["emailAddress"].lower()
        start_history = str(profile["historyId"])
        state: dict[str, Any] = dict(cursor)
        kept = set(state.get("kept_threads", []))
        domains, known = self.context.customer_domains(), self.context.known_emails()

        ids_iter: Iterator[list[str]]
        if state.get("history_id") and not state.get("full_page_token"):
            ids_iter = self._history_ids(state)
        else:
            ids_iter = self._window_ids(state)
        try:
            for ids in ids_iter:
                items = []
                for mid in ids:
                    try:
                        meta = self.http.request(
                            "GET",
                            f"/messages/{mid}",
                            params={"format": "metadata", "metadataHeaders": ["From", "To", "Cc", "Subject"]},
                        )
                    except NotFound:
                        continue  # deleted between list and get
                    if not self._relevant(meta, kept, domains, known):
                        continue
                    full = self.http.request("GET", f"/messages/{mid}", params={"format": "full"})
                    full["_casefile"] = {"mailbox": mailbox}
                    kept.add(full["threadId"])
                    items.append(RawItem("gmail.message", f"{mailbox}:{mid}", full))
                state["kept_threads"] = sorted(kept)[-MAX_KEPT_THREADS:]
                yield Page(items, dict(state))
        except NotFound:
            # The saved history id is too old: rescan the window next run.
            state.pop("history_id", None)
            yield Page([], dict(state))
            return
        state.pop("full_page_token", None)
        state["history_id"] = start_history
        state["mailbox"] = mailbox
        yield Page([], dict(state))

    def _window_ids(self, state: dict[str, Any]) -> Iterator[list[str]]:
        after = (datetime.now(UTC) - timedelta(days=self.window_days)).strftime("%Y/%m/%d")
        token = state.get("full_page_token")
        while True:
            params: dict[str, Any] = {
                "q": f"after:{after} -in:spam -in:trash -in:drafts",
                "maxResults": self.page_size,
            }
            if token:
                params["pageToken"] = token
            data = self.http.request("GET", "/messages", params=params)
            token = data.get("nextPageToken")
            state["full_page_token"] = token
            yield [m["id"] for m in data.get("messages", [])]
            if not token:
                return

    def _history_ids(self, state: dict[str, Any]) -> Iterator[list[str]]:
        token = None
        while True:
            params: dict[str, Any] = {"startHistoryId": state["history_id"], "historyTypes": "messageAdded"}
            if token:
                params["pageToken"] = token
            data = self.http.request("GET", "/history", params=params)
            ids = [m["message"]["id"] for h in data.get("history", []) for m in h.get("messagesAdded", [])]
            yield list(dict.fromkeys(ids))
            token = data.get("nextPageToken")
            if not token:
                return
