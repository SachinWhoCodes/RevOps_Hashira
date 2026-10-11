"""In-memory writers for executor tests: they record every write and can be found again by marker."""

from __future__ import annotations

from typing import Any

from casefile.executor.writers.base import CannotVerify, ItemView, WriteResult


class FakeWriter:
    def __init__(self, system: str, *, verifiable: bool = True) -> None:
        self.system = system
        self.verifiable = verifiable
        self.objects: dict[str, dict[str, Any]] = {}  # marker -> payload
        self.writes: list[ItemView] = []
        self.fields: dict[str, Any] = {}  # live field values for field updates (e.g. hs_next_step)
        self.fail: Exception | None = None  # raised by the next write

    def write(self, item: ItemView) -> WriteResult:
        if self.fail is not None:
            exc, self.fail = self.fail, None
            raise exc
        self.writes.append(item)
        if item.action_type == "crm_next_step":
            self.fields[item.target_ref.get("property", "hs_next_step")] = item.payload["value"]
        self.objects[item.marker] = item.payload
        return WriteResult(f"{self.system}-{len(self.writes)}", {"object": item.action_type})

    def find(self, item: ItemView) -> WriteResult | None:
        if not self.verifiable:
            raise CannotVerify("fake: no search scope")
        if item.marker in self.objects:
            return WriteResult(f"{self.system}-found", {"found_by": "marker"})
        return None

    def current(self, item: ItemView) -> dict[str, Any]:
        prop = item.target_ref.get("property", "hs_next_step")
        return {prop: self.fields.get(prop)}


def fake_writers() -> dict[str, FakeWriter]:
    return {s: FakeWriter(s) for s in ("hubspot", "gmail", "slack", "casefile")}
