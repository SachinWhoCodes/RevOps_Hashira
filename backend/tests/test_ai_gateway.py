"""Gateway behaviour with a fake client: kill switch, cache, budget, refusals, fallback, masking, logging."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.ai.gateway import FALLBACK_BETA, AiGateway
from casefile.bootstrap import ensure_org
from casefile.db.models import LlmCache, LlmCall
from tests.fake_ai import FakeClient, response


class Out(BaseModel):
    answer: str


@pytest.fixture
def org(db: Session):  # type: ignore[no-untyped-def]
    return ensure_org(db, slug="ai-test", name="AI test", timezone="UTC", internal_domains=["vendor.test"])


def gw(db: Session, org, client: FakeClient, **settings: object) -> AiGateway:  # type: ignore[no-untyped-def]
    g = AiGateway(db, org.id, client=client)
    if settings:
        g.settings = g.settings.model_copy(update=settings)
    return g


def call(g: AiGateway, role: str = "reader", content: str = "hello") -> object:
    return g.structured(
        purpose="t", role=role, system="sys", content=content, schema=Out, prompt_version="p.v1"
    )


def test_reader_uses_haiku_without_fallback_and_logs_cost(db: Session, org) -> None:  # type: ignore[no-untyped-def]
    client = FakeClient(lambda kw: response(Out(answer="ok")))
    r = call(gw(db, org, client))
    assert r.ok and r.output == Out(answer="ok")  # type: ignore[attr-defined]
    sent = client.calls[0]
    assert sent["model"] == "claude-haiku-5-5" and "fallbacks" not in sent
    assert sent["output_config"] == {"effort": "low"} and sent["output_format"] is Out
    row = db.scalars(select(LlmCall)).one()
    assert row.ok and row.input_tokens == 100 and float(row.cost_usd) > 0


def test_writer_uses_opus_with_server_side_fallback(db: Session, org) -> None:  # type: ignore[no-untyped-def]
    client = FakeClient(lambda kw: response(Out(answer="ok"), model="claude-opus-5-5"))
    assert call(gw(db, org, client), role="writer").ok  # type: ignore[attr-defined]
    sent = client.calls[0]
    assert sent["model"] == "claude-opus-5-5"
    assert sent["fallbacks"] == "default" and sent["betas"] == [FALLBACK_BETA]


def test_identical_input_is_served_from_cache(db: Session, org) -> None:  # type: ignore[no-untyped-def]
    client = FakeClient(lambda kw: response(Out(answer="ok")))
    g = gw(db, org, client)
    call(g)
    second = call(g)
    assert len(client.calls) == 1 and second.cached  # type: ignore[attr-defined]
    assert db.scalar(select(func.count()).select_from(LlmCache)) == 1


def test_kill_switch_makes_no_call(db: Session, org) -> None:  # type: ignore[no-untyped-def]
    client = FakeClient(lambda kw: response(Out(answer="ok")))
    r = call(gw(db, org, client, ai_enabled=False))
    assert not r.ok and r.error == "ai_disabled" and client.calls == []  # type: ignore[attr-defined]


def test_budget_stop(db: Session, org) -> None:  # type: ignore[no-untyped-def]
    client = FakeClient(lambda kw: response(Out(answer="ok")))
    r = call(gw(db, org, client, ai_daily_budget_usd=0.0))
    assert r.error == "budget_exceeded" and client.calls == []  # type: ignore[attr-defined]


def test_refusal_is_a_logged_failure_and_not_cached(db: Session, org) -> None:  # type: ignore[no-untyped-def]
    client = FakeClient(lambda kw: response(None, stop="refusal", category="cyber"))
    r = call(gw(db, org, client))
    assert not r.ok and r.error == "refusal:cyber"  # type: ignore[attr-defined]
    assert db.scalar(select(func.count()).select_from(LlmCache)) == 0
    assert db.scalars(select(LlmCall)).one().ok is False


def test_personal_data_is_masked_before_sending(db: Session, org) -> None:  # type: ignore[no-untyped-def]
    client = FakeClient(lambda kw: response(Out(answer="ok")))
    call(gw(db, org, client), content="Call +91 98765 43210 or mail dana@acme.test about 15 seats")
    sent = client.calls[0]["messages"][0]["content"]
    assert "98765" not in sent and "dana@acme.test" not in sent and "15 seats" in sent


def test_batch_round_trip(db: Session, org) -> None:  # type: ignore[no-untyped-def]
    client = FakeClient(lambda kw: response(Out(answer="ok")))
    client.batch_responder = lambda req: SimpleNamespace(
        type="succeeded",
        message=SimpleNamespace(
            model="claude-haiku-5-5",
            stop_reason="end_turn",
            usage=response(None).usage,
            content=[SimpleNamespace(type="text", text='{"answer": "batched"}')],
        ),
    )
    g = gw(db, org, client)
    batch_id, ready, keys = g.submit_batch(
        purpose="t",
        role="reader",
        system="sys",
        items=[("a", "one"), ("b", "two")],
        schema=Out,
        prompt_version="p.v1",
    )
    assert batch_id == "batch_1" and not ready and set(keys) == {"a", "b"}
    assert "format" in client.batch_requests[0]["params"]["output_config"]
    results = g.collect_batch(
        batch_id, purpose="t", schema=Out, prompt_version="p.v1", keys=keys, sleep=lambda s: None
    )
    assert results["a"].output == Out(answer="batched")
    costs = [float(c.cost_usd) for c in db.scalars(select(LlmCall))]
    assert len(costs) == 2 and all(c > 0 for c in costs)
    # A second submit of the same items is answered from the cache, nothing is sent.
    again, ready2, _ = g.submit_batch(
        purpose="t", role="reader", system="sys", items=[("a", "one")], schema=Out, prompt_version="p.v1"
    )
    assert again is None and ready2["a"].cached
