"""Contract tests for live readers against mocked HTTP in the vendors' documented shapes.

These prove our request/paging/cursor logic. They do NOT prove the live APIs behave as documented —
that is spikes S1/S2 with real sandbox credentials.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from casefile.connectors.base import AuthError, ConnectorError, NotFound, RateLimited
from casefile.connectors.google.calendar import CalendarReader
from casefile.connectors.google.gmail import GmailReader
from casefile.connectors.http import HttpSource
from casefile.connectors.hubspot.reader import HubSpotReader
from casefile.ingest.util import field_mapping


class Ctx:
    def customer_domains(self) -> set[str]:
        return {"acme.test"}

    def known_emails(self) -> set[str]:
        return {"pat@gmail.com"}

    def internal_domains(self) -> set[str]:
        return {"vendor.test"}


def _src(name: str, base: str) -> HttpSource:
    return HttpSource(name, httpx.Client(base_url=base), sleep=lambda _s: None)


# ---------------------------------------------------------------- HttpSource


@respx.mock
def test_http_retries_429_with_retry_after_then_succeeds() -> None:
    route = respx.get("https://x.test/a").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(200, json={"ok": 1})]
    )
    assert _src("x", "https://x.test").request("GET", "/a") == {"ok": 1}
    assert route.call_count == 2


@respx.mock
def test_http_gives_up_on_long_retry_after() -> None:
    respx.get("https://x.test/a").mock(return_value=httpx.Response(429, headers={"Retry-After": "600"}))
    with pytest.raises(RateLimited):
        _src("x", "https://x.test").request("GET", "/a")


@respx.mock
def test_http_auth_and_not_found_are_distinct() -> None:
    respx.get("https://x.test/auth").mock(return_value=httpx.Response(401))
    respx.get("https://x.test/gone").mock(return_value=httpx.Response(410))
    respx.get("https://x.test/bad").mock(return_value=httpx.Response(400, text="nope"))
    src = _src("x", "https://x.test")
    with pytest.raises(AuthError):
        src.request("GET", "/auth")
    with pytest.raises(NotFound):
        src.request("GET", "/gone")
    with pytest.raises(ConnectorError) as err:
        src.request("GET", "/bad")
    assert err.value.retryable is False


@respx.mock
def test_http_retries_server_errors() -> None:
    route = respx.get("https://x.test/a").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={})]
    )
    _src("x", "https://x.test").request("GET", "/a")
    assert route.call_count == 2


# ---------------------------------------------------------------- HubSpot


def _company(i: int) -> dict:
    return {"id": str(i), "properties": {"name": f"C{i}"}, "propertiesWithHistory": {}}


@respx.mock
def test_hubspot_full_sync_pages_and_sets_since() -> None:
    base = "https://api.hubapi.com"
    respx.get(f"{base}/crm/v3/owners").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "9", "email": "o@vendor.test"}]})
    )
    companies = respx.get(f"{base}/crm/v3/objects/companies").mock(
        side_effect=[
            httpx.Response(200, json={"results": [_company(1)], "paging": {"next": {"after": "1"}}}),
            httpx.Response(200, json={"results": [_company(2)]}),
        ]
    )
    for obj in ("contacts", "deals", "notes", "tasks", "calls", "emails", "meetings"):
        respx.get(f"{base}/crm/v3/objects/{obj}").mock(return_value=httpx.Response(200, json={"results": []}))
    reader = HubSpotReader("t", field_mapping(), http=_src("hubspot", base))
    pages = list(reader.pages({}))
    items = [i for p in pages for i in p.items]
    assert [i.object_type for i in items] == ["hubspot.owner", "hubspot.company", "hubspot.company"]
    params = dict(companies.calls[0].request.url.params)
    assert "propertiesWithHistory" in params and "renewal_date" in params["propertiesWithHistory"]
    assert companies.calls[1].request.url.params["after"] == "1"
    final = pages[-1].cursor
    assert set(final) >= {"companies", "contacts", "deals"} and all("since" in v for v in final.values())


@respx.mock
def test_hubspot_incremental_uses_search_then_fetches_history() -> None:
    base = "https://api.hubapi.com"
    respx.get(f"{base}/crm/v3/owners").mock(return_value=httpx.Response(200, json={"results": []}))
    search = respx.post(f"{base}/crm/v3/objects/companies/search").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "7"}]})
    )
    respx.get(f"{base}/crm/v3/objects/companies/7").mock(return_value=httpx.Response(200, json=_company(7)))
    objects = ("contacts", "deals", "notes", "tasks", "calls", "emails", "meetings")
    for obj in objects:
        respx.post(f"{base}/crm/v3/objects/{obj}/search").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
    since = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    cursor = {k: {"since": since} for k in ("companies", *objects)}
    items = [
        i
        for p in HubSpotReader("t", field_mapping(), http=_src("hubspot", base)).pages(cursor)
        for i in p.items
    ]
    assert [i.external_id for i in items] == ["7"]
    body = json.loads(search.calls[0].request.content)
    assert body["filterGroups"][0]["filters"][0]["operator"] == "GTE"


# ---------------------------------------------------------------- Gmail


def _meta(mid: str, thread: str, frm: str, to: str = "me@vendor.test", subject: str = "Hi") -> dict:
    headers = [
        {"name": "From", "value": frm},
        {"name": "To", "value": to},
        {"name": "Subject", "value": subject},
    ]
    return {"id": mid, "threadId": thread, "payload": {"headers": headers}}


@respx.mock
def test_gmail_fetches_full_message_only_when_relevant() -> None:
    base = "https://gmail.googleapis.com/gmail/v1/users/me"
    respx.get(f"{base}/profile").mock(
        return_value=httpx.Response(200, json={"emailAddress": "Me@vendor.test", "historyId": "500"})
    )
    respx.get(f"{base}/messages").mock(
        return_value=httpx.Response(
            200, json={"messages": [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}]}
        )
    )
    metas = {
        "a": _meta("a", "t1", "dana@acme.test"),  # customer domain → keep
        "b": _meta("b", "t2", "newsletter@shop.test"),  # unrelated → never fetched in full
        "c": _meta("c", "t1", "dana.home@gmail.com"),  # same thread as a kept one → keep
        "d": _meta("d", "t3", "pat@gmail.com"),  # known contact on free-mail → keep
    }
    full_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        mid = request.url.path.rsplit("/", 1)[-1]
        if request.url.params.get("format") == "full":
            full_calls.append(mid)
            return httpx.Response(200, json={**metas[mid], "internalDate": "1700000000000"})
        return httpx.Response(200, json=metas[mid])

    respx.get(url__regex=rf"{base}/messages/\w+").mock(side_effect=handler)
    reader = GmailReader(token=None, context=Ctx(), http=_src("gmail", base))  # type: ignore[arg-type]
    pages = list(reader.pages({}))
    assert sorted(full_calls) == ["a", "c", "d"]
    items = [i for p in pages for i in p.items]
    assert all(i.payload["_casefile"]["mailbox"] == "me@vendor.test" for i in items)
    assert pages[-1].cursor["history_id"] == "500"


@respx.mock
def test_gmail_expired_history_falls_back_to_full_scan_next_run() -> None:
    base = "https://gmail.googleapis.com/gmail/v1/users/me"
    respx.get(f"{base}/profile").mock(
        return_value=httpx.Response(200, json={"emailAddress": "me@vendor.test", "historyId": "900"})
    )
    respx.get(f"{base}/history").mock(return_value=httpx.Response(404))
    reader = GmailReader(token=None, context=Ctx(), http=_src("gmail", base))  # type: ignore[arg-type]
    pages = list(reader.pages({"history_id": "1"}))
    assert "history_id" not in pages[-1].cursor


# ---------------------------------------------------------------- Calendar


@respx.mock
def test_calendar_keeps_external_and_cancelled_and_saves_sync_token() -> None:
    base = "https://www.googleapis.com/calendar/v3"
    respx.get(f"{base}/calendars/primary").mock(
        return_value=httpx.Response(200, json={"id": "me@vendor.test"})
    )
    events = respx.get(f"{base}/calendars/primary/events").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"id": "e1", "attendees": [{"email": "me@vendor.test"}, {"email": "dana@acme.test"}]},
                    {"id": "e2", "attendees": [{"email": "me@vendor.test"}, {"email": "boss@vendor.test"}]},
                    {"id": "e3", "status": "cancelled"},
                ],
                "nextSyncToken": "tok",
            },
        )
    )
    reader = CalendarReader(token=None, context=Ctx(), http=_src("gcal", base))  # type: ignore[arg-type]
    pages = list(reader.pages({}))
    assert [i.payload["id"] for p in pages for i in p.items] == ["e1", "e3"]
    assert pages[-1].cursor["sync_token"] == "tok"
    assert "timeMin" in events.calls[0].request.url.params


@respx.mock
def test_calendar_expired_sync_token_restarts_full_sync() -> None:
    base = "https://www.googleapis.com/calendar/v3"
    respx.get(f"{base}/calendars/primary").mock(
        return_value=httpx.Response(200, json={"id": "me@vendor.test"})
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "syncToken" in request.url.params:
            return httpx.Response(410)
        return httpx.Response(200, json={"items": [], "nextSyncToken": "new"})

    respx.get(f"{base}/calendars/primary/events").mock(side_effect=handler)
    reader = CalendarReader(token=None, context=Ctx(), http=_src("gcal", base))  # type: ignore[arg-type]
    assert list(reader.pages({"sync_token": "old"}))[-1].cursor["sync_token"] == "new"
