"""Writer contracts with mocked HTTP: request shapes, outcome classification, no send path, R1."""

from __future__ import annotations

import base64
import email
import json
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
import pytest
import respx

from casefile.actions.spec import marker
from casefile.executor.writers.base import CannotVerify, ItemView, WriteRejected, WriteUnknown
from casefile.executor.writers.gmail import GmailDraftWriter
from casefile.executor.writers.hubspot import HubSpotWriter
from casefile.executor.writers.slack import SlackWriter

ITEM_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")
BACKEND = Path(__file__).resolve().parents[1]


def view(action: str, payload: dict, target: dict | None = None, before: dict | None = None) -> ItemView:
    return ItemView(ITEM_ID, action, target or {}, before or {}, payload, marker(ITEM_ID))


HS_TARGET = {"company": "111", "deal": "222", "owner": "333"}


@respx.mock
def test_hubspot_task_shape() -> None:
    route = respx.post("https://api.hubapi.com/crm/v3/objects/tasks").mock(
        return_value=httpx.Response(201, json={"id": "9001"})
    )
    w = HubSpotWriter("t")
    item = view(
        "crm_task",
        {"subject": "Call Dana", "body": "<p>why</p>", "due_date": "2026-10-14", "priority": "HIGH"},
        HS_TARGET,
    )
    result = w.write(item)
    sent = json.loads(route.calls[0].request.content)
    props = sent["properties"]
    assert result.external_id == "9001"
    assert props["hs_task_subject"] == "Call Dana" and props["hs_task_priority"] == "HIGH"
    assert props["hubspot_owner_id"] == "333" and props["hs_timestamp"] == "2026-10-14T12:00:00Z"
    assert item.marker in props["hs_task_body"]
    assoc = {(a["to"]["id"], a["types"][0]["associationTypeId"]) for a in sent["associations"]}
    assert assoc == {("111", 192), ("222", 216)}
    assert route.calls[0].request.headers["authorization"] == "Bearer t"


@respx.mock
def test_hubspot_note_and_next_step() -> None:
    note = respx.post("https://api.hubapi.com/crm/v3/objects/notes").mock(
        return_value=httpx.Response(201, json={"id": "77"})
    )
    patch = respx.patch("https://api.hubapi.com/crm/v3/objects/deals/222").mock(
        return_value=httpx.Response(200, json={"id": "222"})
    )
    w = HubSpotWriter("t")
    w.write(view("crm_note", {"body": "<p>summary</p>"}, HS_TARGET))
    sent = json.loads(note.calls[0].request.content)
    assert {a["types"][0]["associationTypeId"] for a in sent["associations"]} == {190, 214}
    w.write(view("crm_next_step", {"value": "Call Dana"}, {**HS_TARGET, "property": "hs_next_step"}))
    assert json.loads(patch.calls[0].request.content) == {"properties": {"hs_next_step": "Call Dana"}}


@respx.mock
def test_hubspot_find_by_marker_uses_contains_token() -> None:
    item = view("crm_task", {"subject": "s", "body": "b", "due_date": "2026-10-14"}, HS_TARGET)
    search = respx.post("https://api.hubapi.com/crm/v3/objects/tasks/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"id": "9001", "properties": {"hs_task_body": f"x Casefile ref {item.marker}"}}]
            },
        )
    )
    found = HubSpotWriter("t").find(item)
    assert found is not None and found.external_id == "9001"
    flt = json.loads(search.calls[0].request.content)["filterGroups"][0]["filters"][0]
    assert flt == {"propertyName": "hs_task_body", "operator": "CONTAINS_TOKEN", "value": item.marker}


@respx.mock
def test_outcome_classification() -> None:
    url = "https://api.hubapi.com/crm/v3/objects/tasks"
    item = view("crm_task", {"subject": "s", "body": "b", "due_date": "2026-10-14"}, HS_TARGET)
    w = HubSpotWriter("t")
    respx.post(url).mock(return_value=httpx.Response(400, json={"message": "bad property"}))
    with pytest.raises(WriteRejected) as rejected:
        w.write(item)
    assert not rejected.value.retryable
    respx.post(url).mock(return_value=httpx.Response(429))
    with pytest.raises(WriteRejected) as limited:
        w.write(item)
    assert limited.value.retryable
    respx.post(url).mock(return_value=httpx.Response(502))
    with pytest.raises(WriteUnknown):
        w.write(item)
    respx.post(url).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(WriteUnknown):
        w.write(item)
    respx.post(url).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(WriteRejected) as down:
        w.write(item)
    assert down.value.retryable


@respx.mock
def test_gmail_creates_a_draft_with_our_message_id() -> None:
    route = respx.post("https://gmail.googleapis.com/gmail/v1/users/me/drafts").mock(
        return_value=httpx.Response(200, json={"id": "r-1", "message": {"id": "m-1", "threadId": "t-1"}})
    )
    w = GmailDraftWriter(client=httpx.Client(base_url="https://gmail.googleapis.com/gmail/v1/users/me"))
    item = view(
        "gmail_draft",
        {"to": ["dana@acme-corp.test"], "subject": "Checking in", "body": "Hi Dana"},
        {"mailbox": "me@lumora.test"},
    )
    result = w.write(item)
    raw = json.loads(route.calls[0].request.content)["message"]["raw"]
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert result.external_id == "r-1"
    assert msg["To"] == "dana@acme-corp.test" and msg["Subject"] == "Checking in"
    assert msg["Message-ID"] == f"<{item.marker}@casefile.local>" and msg["X-Casefile-Ref"] == item.marker


def test_gmail_writer_has_no_send_path() -> None:
    source = (BACKEND / "casefile/executor/writers/gmail.py").read_text()
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith(("#", '"""')))
    for forbidden in ("/send", "messages/send", "drafts/send", ".send("):
        assert forbidden not in code
    assert not hasattr(GmailDraftWriter, "send")


@respx.mock
def test_slack_dm_shape_and_errors() -> None:
    respx.post("https://slack.com/api/conversations.open").mock(
        return_value=httpx.Response(200, json={"ok": True, "channel": {"id": "D1"}})
    )
    post = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "1.2"})
    )
    w = SlackWriter("xoxb-test")
    item = view("slack_dm", {"text": "hello"}, {"user_id": "U0"})
    assert w.write(item).external_id == "1.2"
    sent = json.loads(post.calls[0].request.content)
    assert sent["channel"] == "D1" and sent["metadata"]["event_payload"] == {"ref": item.marker}
    post.mock(return_value=httpx.Response(200, json={"ok": False, "error": "channel_not_found"}))
    with pytest.raises(WriteRejected):
        w.write(item)


@respx.mock
def test_slack_find_without_history_scope_cannot_verify() -> None:
    respx.post("https://slack.com/api/conversations.open").mock(
        return_value=httpx.Response(200, json={"ok": True, "channel": {"id": "D1"}})
    )
    respx.get("https://slack.com/api/conversations.history").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "missing_scope", "needed": "im:history"})
    )
    with pytest.raises(CannotVerify):
        SlackWriter("xoxb-test").find(view("slack_dm", {"text": "hello"}, {"user_id": "U0"}))


def test_write_credentials_never_reach_the_api_or_worker() -> None:
    """Rule R1: the app's settings have no write fields, and importing the API or worker does not
    load the executor or its writers."""
    from casefile.config import Settings

    assert not [f for f in Settings.model_fields if f.startswith("write_") or "slack_bot" in f]
    probe = (
        "import sys, casefile.api.main, casefile.worker.__main__;"
        "print(sorted(m for m in sys.modules if m.startswith('casefile.executor')))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, cwd=BACKEND, check=True
    )
    assert out.stdout.strip() == "[]", out.stdout


@respx.mock
def test_gmail_find_uses_drafts_get_and_our_header() -> None:
    """Gmail rewrites the Message-ID on drafts and messages.get is outside gmail.compose (observed
    live), so the lookup scans recent drafts with drafts.get and matches X-Casefile-Ref."""
    item = view("gmail_draft", {"to": ["a@b.test"], "subject": "s", "body": "b"}, {"mailbox": "me@x.test"})
    base = "https://gmail.googleapis.com/gmail/v1/users/me"
    respx.get(f"{base}/drafts").mock(
        return_value=httpx.Response(200, json={"drafts": [{"id": "r-1"}, {"id": "r-2"}]})
    )
    respx.get(f"{base}/drafts/r-1").mock(
        return_value=httpx.Response(
            200, json={"message": {"payload": {"headers": [{"name": "X-Casefile-Ref", "value": "other"}]}}}
        )
    )
    respx.get(f"{base}/drafts/r-2").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"payload": {"headers": [{"name": "X-Casefile-Ref", "value": item.marker}]}}},
        )
    )
    messages = respx.get(url__startswith=f"{base}/messages").mock(return_value=httpx.Response(403))
    found = GmailDraftWriter(client=httpx.Client(base_url=base)).find(item)
    assert found is not None and found.external_id == "r-2"
    assert not messages.called
