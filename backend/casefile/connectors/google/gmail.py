"""Gmail reader (gmail.readonly), one connection per opted-in mailbox.

Data minimisation: for each candidate message we first fetch headers only, and fetch the full
message only when a participant belongs to a known customer domain or contact, when it continues a
thread we already keep, or when it is a delivery-failure notice. Everything else is never fetched.

The mailbox is never listed in full. The reader asks Gmail for *threads* to or from customer domains
and known contacts within the window (ten addresses per search), and fetches each matching thread
whole, a few in parallel. With no customers yet it reads nothing at all. Later runs read new mail from
``history.list`` (headers first, the full message only when relevant). The cursor remembers, as short
hashes, which addresses were searched for; customers the CRM adds later (e.g. the mailbox was
connected first) are searched for on the next run, so their past emails are not lost. An expired
history id (404) means every customer is searched for again.

Status: contract-tested with mocked HTTP; run live on 2026-10-11 against a real mailbox (profile,
listing, header reads, history) with nothing kept (no customers in the rehearsal organisation).
"""

from __future__ import annotations

import hashlib
from collections.abc import Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
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


CATCH_UP_PER_QUERY = 10  # addresses per Gmail search (each adds three terms to the query)
# Parallel GETs. Gmail allows 15,000 quota units per user per minute and a thread fetch costs 10:
# eight at a time exceeded it on a real mailbox (observed); four, plus backoff on 403 quota errors.
FETCH_WORKERS = 4
META_HEADERS = ["From", "To", "Cc", "Subject"]
SKIP_LABELS = {"SPAM", "TRASH", "DRAFT"}


def target_hashes(domains: set[str], known: set[str]) -> list[str]:
    """What the mailbox has been searched for, without storing customer names in the cursor."""
    return sorted(hashlib.sha256(t.encode()).hexdigest()[:12] for t in domains | known)


def matches(address: str, domains: set[str], known: set[str]) -> bool:
    """A known customer contact, or an address at a customer domain (subdomains included)."""
    address = address.lower()
    if address in known:
        return True
    d = email_domain(address)
    while d.count(".") >= 1:
        if d in domains:
            return True
        d = d.split(".", 1)[1]
    return False


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
        bounce = (
            any(is_system_address(a) for a in addrs[:1]) and "deliver" in headers.get("subject", "").lower()
        )
        if bounce and known:
            return True  # bounce notice: detects a champion who left (only once there are customer contacts)
        # Automated senders (no-reply@, postmaster@ …) never count as customer engagement, so a message
        # is kept for a human customer address only (observed: Google's security alerts otherwise kept).
        return any(matches(a, domains, known) for a in addrs if not is_system_address(a))

    # ------------------------------------------------------------ pages

    def pages(self, cursor: dict[str, Any]) -> Iterator[Page]:
        profile = self.http.request("GET", "/profile")
        mailbox = profile["emailAddress"].lower()
        start_history = str(profile["historyId"])
        state: dict[str, Any] = {k: v for k, v in cursor.items() if k != "full_page_token"}
        kept = set(state.get("kept_threads", []))
        domains, known = self.context.customer_domains(), self.context.known_emails()
        targets = target_hashes(domains, known)
        first = not state.get("history_id")
        searched = set() if first else set(state.get("searched", []))
        new = sorted(a for a in domains | known if target_hashes({a}, set())[0] not in searched)
        queries = [
            " ".join(f"from:{t} to:{t} cc:{t}" for t in new[i : i + CATCH_UP_PER_QUERY])
            for i in range(0, len(new), CATCH_UP_PER_QUERY)
        ]
        if known and not state.get("bounces_searched"):
            queries.append("from:mailer-daemon from:postmaster")
        try:
            # 1. Customers this mailbox was never searched for (all of them on the first run): whole
            #    threads, so a reply from someone's personal address stays with its conversation.
            seen: set[str] = set()
            for thread_ids in self._thread_ids(queries):
                fresh = [t for t in thread_ids if t not in seen]
                seen.update(fresh)
                items = []
                for thread in self._fetch(fresh, "threads", {"format": "full"}):
                    if thread is None:
                        continue  # deleted between search and fetch
                    messages = [
                        m for m in thread.get("messages", []) if not SKIP_LABELS & set(m.get("labelIds", []))
                    ]
                    if not any(self._relevant(m, kept, domains, known) for m in messages):
                        continue  # a search false positive: read, not kept
                    kept.add(thread["id"])
                    for m in messages:
                        m["_casefile"] = {"mailbox": mailbox}
                        items.append(RawItem("gmail.message", f"{mailbox}:{m['id']}", m))
                state["kept_threads"] = sorted(kept)[-MAX_KEPT_THREADS:]
                yield Page(items, dict(state))
            if known:
                state["bounces_searched"] = True
            # 2. New mail since the last run: headers first, the full message only when relevant.
            if not first:
                for ids in self._history_ids(state):
                    metas = self._fetch(
                        ids, "messages", {"format": "metadata", "metadataHeaders": META_HEADERS}
                    )
                    wanted = [m["id"] for m in metas if m and self._relevant(m, kept, domains, known)]
                    items = []
                    for full in self._fetch(wanted, "messages", {"format": "full"}):
                        if full is None:
                            continue
                        full["_casefile"] = {"mailbox": mailbox}
                        kept.add(full["threadId"])
                        items.append(RawItem("gmail.message", f"{mailbox}:{full['id']}", full))
                    state["kept_threads"] = sorted(kept)[-MAX_KEPT_THREADS:]
                    yield Page(items, dict(state))
        except NotFound:
            # The saved history id is too old: search every customer again next run.
            state.pop("history_id", None)
            state["searched"] = []
            yield Page([], dict(state))
            return
        state["history_id"] = start_history
        state["mailbox"] = mailbox
        state["searched"] = targets
        yield Page([], dict(state))

    def _fetch(self, ids: list[str], kind: str, params: dict[str, Any]) -> list[dict[str, Any] | None]:
        """GET /{kind}/{id} for each id, a few at a time (one at a time took ~0.44 s per message, observed);
        an id deleted in the meantime gives None."""

        def one(item_id: str) -> dict[str, Any] | None:
            try:
                return self.http.request("GET", f"/{kind}/{item_id}", params=params)
            except NotFound:
                return None

        if len(ids) <= 1:
            return [one(i) for i in ids]
        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
            return list(pool.map(one, ids))

    def _thread_ids(self, queries: list[str]) -> Iterator[list[str]]:
        after = (datetime.now(UTC) - timedelta(days=self.window_days)).strftime("%Y/%m/%d")
        for terms in queries:
            token = None
            while True:
                params: dict[str, Any] = {
                    "q": f"after:{after} -in:spam -in:trash -in:drafts {{{terms}}}",
                    "maxResults": self.page_size,
                }
                if token:
                    params["pageToken"] = token
                data = self.http.request("GET", "/threads", params=params)
                yield [t["id"] for t in data.get("threads", [])]
                token = data.get("nextPageToken")
                if not token:
                    break

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
