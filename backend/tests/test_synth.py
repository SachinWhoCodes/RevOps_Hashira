from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from casefile.synth.generator import SynthParams, generate
from tests.conftest import TEST_TODAY


def _digest(directory: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(directory.glob("*.jsonl")):
        h.update(f.read_bytes())
    return h.hexdigest()


def _rows(directory: Path, stream: str) -> list[dict]:
    return [json.loads(line) for line in (directory / f"{stream}.jsonl").read_text().splitlines()]


def _header(msg: dict, name: str) -> str | None:
    return next((h["value"] for h in msg["payload"]["headers"] if h["name"] == name), None)


def test_generator_is_deterministic(tmp_path: Path) -> None:
    params = SynthParams(seed=3, n_accounts=12, history_days=200, today=TEST_TODAY)
    generate(params, tmp_path / "a")
    generate(params, tmp_path / "b")
    assert _digest(tmp_path / "a") == _digest(tmp_path / "b")


def test_only_reserved_customer_domains(small_synth: Path) -> None:
    truth = json.loads((small_synth / "ground_truth.json").read_text())
    for account in truth["accounts"].values():
        assert all(d.endswith(".test") for d in account["domains"]), account["domains"]


def test_acme_matches_the_blueprint_case(small_synth: Path) -> None:
    truth = json.loads((small_synth / "ground_truth.json").read_text())
    acme_id, acme = next((k, a) for k, a in truth["accounts"].items() if a["hero"])
    current = acme["periods"][-1]
    assert date.fromisoformat(current["renewal"]) == TEST_TODAY + timedelta(days=43)
    assert current["story"] == "silent_churn"

    msgs = [
        m
        for m in _rows(small_synth, "gmail_messages")
        if "acme-corp.test" in (_header(m, "To") or "") + (_header(m, "From") or "")
    ]
    latest_inbound = max(
        int(m["internalDate"]) for m in msgs if "acme-corp.test" in (_header(m, "From") or "")
    )
    unanswered = [m for m in msgs if int(m["internalDate"]) > latest_inbound]
    days_ago = sorted(
        (TEST_TODAY - datetime.fromtimestamp(int(m["internalDate"]) / 1000).date()).days for m in unanswered
    )
    assert 16 in days_ago and 9 in days_ago

    events = [e for e in _rows(small_synth, "gcal_events") if e["summary"].startswith("Acme")]
    held = [
        e for e in events if e["status"] == "confirmed" and e["start"]["dateTime"][:10] <= str(TEST_TODAY)
    ]
    assert max(e["start"]["dateTime"][:10] for e in held) == str(TEST_TODAY - timedelta(days=21))
    assert not [e for e in events if e["start"]["dateTime"][:10] > str(TEST_TODAY)]

    deal = next(d for d in _rows(small_synth, "hubspot_deals") if d["id"] == current["deal_id"])
    assert deal["properties"]["hs_next_step"] == ""
    assert acme_id == deal["associations"]["companies"]["results"][0]["id"]


def test_no_record_dated_after_today(small_synth: Path) -> None:
    """The generator itself must not leak future data (future *scheduled* meetings are allowed,
    but they must have been created on or before today)."""
    limit = int(datetime.combine(TEST_TODAY, datetime.min.time()).timestamp() * 1000) + 86_400_000
    assert all(int(m["internalDate"]) < limit for m in _rows(small_synth, "gmail_messages"))
    assert all(e["created"][:10] <= str(TEST_TODAY) for e in _rows(small_synth, "gcal_events"))
    assert all(u["date"] < str(TEST_TODAY) for u in _rows(small_synth, "usage_daily"))
    for deal in _rows(small_synth, "hubspot_deals"):
        for history in deal["propertiesWithHistory"].values():
            assert all(h["timestamp"][:10] <= str(TEST_TODAY) for h in history)


def test_every_email_has_ground_truth(small_synth: Path) -> None:
    truth = json.loads((small_synth / "ground_truth.json").read_text())["emails"]
    for msg in _rows(small_synth, "gmail_messages"):
        assert _header(msg, "Message-ID") in truth
