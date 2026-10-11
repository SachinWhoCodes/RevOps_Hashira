"""Load the versioned playbooks and pick the play for a case (plain rules, no AI)."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PLAYBOOKS_PATH = Path(__file__).with_name("playbooks.yaml")


@dataclass(frozen=True)
class Play:
    id: str
    version: str
    kind: str | None
    purpose: str
    actions: tuple[dict[str, Any], ...]
    recipient: str | None = None
    fallback_recipient: str | None = None
    all_of: tuple[str, ...] = field(default_factory=tuple)
    any_of: tuple[str, ...] = field(default_factory=tuple)

    def matches(self, kind: str, signal_types: set[str]) -> bool:
        if self.kind and self.kind != kind:
            return False
        if self.all_of and not set(self.all_of) <= signal_types:
            return False
        return not self.any_of or bool(set(self.any_of) & signal_types)


@lru_cache
def load_playbooks(path: Path = PLAYBOOKS_PATH) -> tuple[list[Play], Play]:
    raw = yaml.safe_load(path.read_text())
    version = f"v{raw['version']}"

    def build(p: dict[str, Any]) -> Play:
        return Play(
            id=p["id"],
            version=version,
            kind=p.get("kind"),
            purpose=" ".join(str(p.get("purpose", "")).split()),
            actions=tuple(p["actions"]),
            recipient=p.get("recipient"),
            fallback_recipient=p.get("fallback_recipient"),
            all_of=tuple(p.get("all_of", ())),
            any_of=tuple(p.get("any_of", ())),
        )

    return [build(p) for p in raw["plays"]], build(raw["fallback"])


def choose_play(kind: str, signal_types: set[str]) -> Play:
    plays, fallback = load_playbooks()
    return next((p for p in plays if p.matches(kind, signal_types)), fallback)
