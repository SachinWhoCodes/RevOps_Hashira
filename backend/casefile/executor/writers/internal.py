"""Tier 0 lines ("review manually") live only inside Casefile; executing them records completion."""

from __future__ import annotations

from typing import Any

from casefile.executor.writers.base import ItemView, WriteResult


class InternalWriter:
    system = "casefile"

    def write(self, item: ItemView) -> WriteResult:
        return WriteResult(None, {"object": "reminder", "noop": True})

    def find(self, item: ItemView) -> WriteResult | None:
        return None

    def current(self, item: ItemView) -> dict[str, Any]:
        return {}
