"""A fake Anthropic client for tests: no network, no cost, records every request."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any


def usage(inp: int = 100, out: int = 50) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=inp, output_tokens=out, cache_read_input_tokens=0, cache_creation_input_tokens=0
    )


def response(
    parsed: Any, *, model: str = "claude-haiku-5-5", stop: str = "end_turn", category: str | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        parsed_output=parsed if stop not in {"refusal", "max_tokens"} else None,
        stop_reason=stop,
        stop_details=SimpleNamespace(category=category) if stop == "refusal" else None,
        model=model,
        usage=usage(),
    )


class _Messages:
    def __init__(self, owner: FakeClient) -> None:
        self.owner = owner
        self.batches = SimpleNamespace(
            create=self.owner.batch_create,
            retrieve=self.owner.batch_retrieve,
            results=self.owner.batch_results,
        )

    def parse(self, **kw: Any) -> Any:
        self.owner.calls.append(kw)
        return self.owner.responder(kw)


class FakeClient:
    def __init__(self, responder: Callable[[dict[str, Any]], Any]) -> None:
        self.responder = responder
        self.calls: list[dict[str, Any]] = []
        self.messages = _Messages(self)
        self.beta = SimpleNamespace(messages=_Messages(self))
        self.batch_requests: list[dict[str, Any]] = []
        self.batch_responder: Callable[[dict[str, Any]], Any] | None = None

    def batch_create(self, requests: list[dict[str, Any]]) -> SimpleNamespace:
        self.batch_requests = requests
        return SimpleNamespace(id="batch_1")

    def batch_retrieve(self, batch_id: str) -> SimpleNamespace:
        return SimpleNamespace(processing_status="ended")

    def batch_results(self, batch_id: str) -> list[SimpleNamespace]:
        assert self.batch_responder is not None
        return [
            SimpleNamespace(custom_id=r["custom_id"], result=self.batch_responder(r))
            for r in self.batch_requests
        ]
