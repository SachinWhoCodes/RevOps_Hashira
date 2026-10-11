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
from casefile.connectors.google.gmail import GmailReader, target_hashes
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

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"


def _meta(mid: str, thread: str, frm: str, to: str = "me@vendor.test", subject: str = "Hi") -> dict:
    headers = [
        {"name": "From", "value": frm},
        {"name": "To", "value": to},
        {"name": "Subject", "value": subject},
    ]
    return {"id": mid, "threadId": thread, "payload": {"headers": headers}}


class NoCustomers(Ctx):
    def customer_domains(self) -> set[str]:
        return set()

    def known_emails(self) -> set[str]:
        return set()


def _profile(history: str = "500") -> None:
    respx.get(f"{GMAIL}/profile").mock(
        return_value=httpx.Response(200, json={"emailAddress": "Me@vendor.test", "historyId": history})
    )


@respx.mock
def test_gmail_first_run_searches_customer_threads_and_keeps_them_whole() -> None:
    _profile()
    threads = {
        "t1": [_meta("a", "t1", "dana@acme.test"), _meta("c", "t1", "dana.home@gmail.com")],  # reply kept
        "t2": [_meta("b", "t2", "newsletter@shop.test")],  # search false positive: read, not kept
        "t3": [_meta("d", "t3", "pat@gmail.com")],  # known contact on free mail
    }
    threads["t1"].append({**_meta("x", "t1", "dana@acme.test"), "labelIds": ["TRASH"]})  # trashed: skipped
    queries: list[str] = []

    def search(request: httpx.Request) -> httpx.Response:
        q = request.url.params["q"]
        queries.append(q)
        found = [] if "mailer-daemon" in q else [{"id": t} for t in threads]
        return httpx.Response(200, json={"threads": found})

    respx.get(f"{GMAIL}/threads").mock(side_effect=search)
    respx.get(url__regex=rf"{GMAIL}/threads/\w+").mock(
        side_effect=lambda r: httpx.Response(
            200,
            json={"id": r.url.path.rsplit("/", 1)[-1], "messages": threads[r.url.path.rsplit("/", 1)[-1]]},
        )
    )
    pages = list(GmailReader(token=None, context=Ctx(), http=_src("gmail", GMAIL)).pages({}))  # type: ignore[arg-type]
    kept = sorted(i.payload["id"] for p in pages for i in p.items)
    assert kept == ["a", "c", "d"]
    assert "from:acme.test" in queries[0] and "from:pat@gmail.com" in queries[0]
    assert any("mailer-daemon" in q for q in queries)  # bounces are searched once there are contacts
    cursor = pages[-1].cursor
    assert cursor["history_id"] == "500" and cursor["bounces_searched"] is True
    assert set(cursor["searched"]) == set(target_hashes({"acme.test"}, {"pat@gmail.com"}))


@respx.mock
def test_gmail_reads_nothing_when_there_are_no_customers() -> None:
    """A mailbox connected before the CRM: no search, no message read; only the history position."""
    _profile("77")
    pages = list(GmailReader(token=None, context=NoCustomers(), http=_src("gmail", GMAIL)).pages({}))  # type: ignore[arg-type]
    assert [i for p in pages for i in p.items] == [] and pages[-1].cursor["history_id"] == "77"
    assert [c.request.url.path for c in respx.calls] == ["/gmail/v1/users/me/profile"]


@respx.mock
def test_gmail_catches_up_on_customers_added_after_the_mailbox() -> None:
    _profile("950")
    search = respx.get(f"{GMAIL}/threads").mock(
        return_value=httpx.Response(200, json={"threads": [{"id": "t9"}]})
    )
    respx.get(f"{GMAIL}/threads/t9").mock(
        return_value=httpx.Response(
            200, json={"id": "t9", "messages": [_meta("old1", "t9", "dana@acme.test")]}
        )
    )
    respx.get(f"{GMAIL}/history").mock(return_value=httpx.Response(200, json={"history": []}))
    already = target_hashes(set(), {"pat@gmail.com"})  # pat was searched for; acme.test is new
    cursor = {"history_id": "900", "searched": already, "bounces_searched": True}
    pages = list(GmailReader(token=None, context=Ctx(), http=_src("gmail", GMAIL)).pages(cursor))  # type: ignore[arg-type]
    q = search.calls[0].request.url.params["q"]
    assert "from:acme.test" in q and "pat@gmail.com" not in q and len(search.calls) == 1
    assert [i.external_id for p in pages for i in p.items] == ["me@vendor.test:old1"]
    assert set(pages[-1].cursor["searched"]) == set(target_hashes({"acme.test"}, {"pat@gmail.com"}))
    assert "acme" not in json.dumps(pages[-1].cursor)  # the cursor holds hashes, not customer names


@respx.mock
def test_gmail_new_mail_is_checked_by_headers_first() -> None:
    _profile("1001")
    respx.get(f"{GMAIL}/history").mock(
        return_value=httpx.Response(
            200, json={"history": [{"messagesAdded": [{"message": {"id": "n1"}}, {"message": {"id": "n2"}}]}]}
        )
    )
    metas = {"n1": _meta("n1", "t1", "dana@acme.test"), "n2": _meta("n2", "t5", "friend@gmail.com")}
    full_calls: list[str] = []

    def message(request: httpx.Request) -> httpx.Response:
        mid = request.url.path.rsplit("/", 1)[-1]
        if request.url.params.get("format") == "full":
            full_calls.append(mid)
        return httpx.Response(200, json=metas[mid])

    respx.get(url__regex=rf"{GMAIL}/messages/\w+").mock(side_effect=message)
    searched = target_hashes(Ctx().customer_domains(), Ctx().known_emails())
    cursor = {"history_id": "1000", "searched": searched, "bounces_searched": True}
    pages = list(GmailReader(token=None, context=Ctx(), http=_src("gmail", GMAIL)).pages(cursor))  # type: ignore[arg-type]
    assert full_calls == ["n1"] and [i.payload["id"] for p in pages for i in p.items] == ["n1"]
    assert pages[-1].cursor["history_id"] == "1001"


@respx.mock
def test_gmail_expired_history_searches_every_customer_again_next_run() -> None:
    _profile("900")
    respx.get(f"{GMAIL}/history").mock(return_value=httpx.Response(404))
    searched = target_hashes(Ctx().customer_domains(), Ctx().known_emails())
    cursor = {"history_id": "1", "searched": searched, "bounces_searched": True}
    pages = list(GmailReader(token=None, context=Ctx(), http=_src("gmail", GMAIL)).pages(cursor))  # type: ignore[arg-type]
    assert "history_id" not in pages[-1].cursor and pages[-1].cursor["searched"] == []


def test_gmail_does_not_keep_automated_mail_from_a_customer_domain() -> None:
    reader = GmailReader(token=None, context=Ctx())  # type: ignore[arg-type]
    alert = _meta("s1", "t8", "no-reply@acme.test", subject="Your weekly report")
    assert reader._relevant(alert, set(), {"acme.test"}, set()) is False
    assert reader._relevant(_meta("s2", "t9", "dana@acme.test"), set(), {"acme.test"}, set()) is True


def test_gmail_ignores_bounces_until_there_are_customer_contacts() -> None:
    reader = GmailReader(token=None, context=Ctx())  # type: ignore[arg-type]
    bounce = _meta(
        "b1", "t1", "mailer-daemon@googlemail.com", subject="Delivery Status Notification (Failure)"
    )
    assert reader._relevant(bounce, set(), set(), {"pat@gmail.com"}) is True
    assert reader._relevant(bounce, set(), set(), set()) is False  # a personal mailbox, no CRM yet


# ---------------------------------------------------------------- Calendar


@respx.mock
def test_calendar_keeps_customer_meetings_and_cancellations_and_saves_sync_token() -> None:
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
                    # an outsider who is not a customer (a friend, a vendor, a candidate): not kept
                    {"id": "e4", "attendees": [{"email": "me@vendor.test"}, {"email": "friend@gmail.com"}]},
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


@respx.mock
def test_calendar_lists_the_window_again_when_new_customers_appear() -> None:
    base = "https://www.googleapis.com/calendar/v3"
    respx.get(f"{base}/calendars/primary").mock(
        return_value=httpx.Response(200, json={"id": "me@vendor.test"})
    )
    events = respx.get(f"{base}/calendars/primary/events").mock(
        return_value=httpx.Response(200, json={"items": [], "nextSyncToken": "fresh"})
    )
    reader = CalendarReader(token=None, context=Ctx(), http=_src("gcal", base))  # type: ignore[arg-type]
    up_to_date = target_hashes({"acme.test"}, {"pat@gmail.com"})
    list(reader.pages({"sync_token": "tok", "searched": up_to_date}))
    assert events.calls[-1].request.url.params.get("syncToken") == "tok"  # nothing new: incremental
    list(reader.pages({"sync_token": "tok", "searched": []}))
    assert "timeMin" in events.calls[-1].request.url.params  # new customers: full window again


def test_a_google_quota_403_is_retried_but_a_missing_scope_403_is_not() -> None:
    from casefile.connectors.base import AuthError
    from casefile.connectors.http import HttpSource

    replies = [
        httpx.Response(
            403,
            json={"error": {"code": 403, "message": "Quota exceeded for quota metric 'Total Query Cost'"}},
        ),
        httpx.Response(200, json={"ok": True}),
    ]
    slept: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: replies.pop(0)), base_url="https://x.test")
    assert HttpSource("gmail", client, sleep=slept.append).request("GET", "/a") == {"ok": True}
    assert slept == [2.0]
    scope = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(403, json={"category": "MISSING_SCOPES"})),
        base_url="https://x.test",
    )
    with pytest.raises(AuthError):
        HttpSource("hubspot", scope, sleep=slept.append).request("GET", "/b")
