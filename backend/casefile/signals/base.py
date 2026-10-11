"""Signal framework.

A signal is a pure function ``(Snapshot, thresholds) -> list[SignalHit]``: no database, no clock,
no AI. Every hit carries:

- ``facts``: every number or date an explanation may cite (Phase 3's checker only lets the AI use
  numbers that appear here),
- ``evidence``: the exact records behind the hit, so a person can click through to them,
- ``summary``: a deterministic one-line sentence, used in the UI and as the fallback explanation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from casefile.db.models import Polarity
from casefile.timeline.snapshot import Snapshot

ENGINE_VERSION = "signals-1"


@dataclass(frozen=True)
class Evidence:
    kind: str  # activity | usage | field | contact | deal
    id: str  # activity id, "deal_id:field", "usage:start:end", ...
    label: str  # short human description shown on the evidence chip
    at: str | None = None  # ISO date/datetime of the record

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "label": self.label, "at": self.at}


@dataclass(frozen=True)
class SignalHit:
    type: str
    polarity: Polarity
    value: float
    summary: str
    facts: dict[str, Any] = field(default_factory=dict)
    evidence: tuple[Evidence, ...] = ()
    window_start: datetime | None = None
    window_end: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "polarity": self.polarity.value,
            "value": self.value,
            "summary": self.summary,
            "facts": self.facts,
            "evidence": [e.to_dict() for e in self.evidence],
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
        }


SignalFn = Callable[[Snapshot, dict[str, Any]], list[SignalHit]]


@dataclass(frozen=True)
class SignalSpec:
    name: str
    polarity: Polarity
    source: str  # which data the signal needs (for blind spots and docs)
    description: str
    fn: SignalFn


REGISTRY: dict[str, SignalSpec] = {}


def signal(name: str, polarity: Polarity, source: str, description: str) -> Callable[[SignalFn], SignalFn]:
    def register(fn: SignalFn) -> SignalFn:
        REGISTRY[name] = SignalSpec(name, polarity, source, description, fn)
        return fn

    return register


@lru_cache
def default_thresholds() -> dict[str, Any]:
    return yaml.safe_load((Path(__file__).parent / "thresholds.yaml").read_text())


def apply_role_fallbacks(snap: Snapshot, thresholds: dict[str, Any] | None = None) -> Snapshot:
    """When the CRM has never recorded a champion for this account, treat the customer contact who
    wrote to us or met us most often (as of the snapshot) as the likely champion, marked inferred.
    The economic buyer keeps their role. Pure and as-of: the back-test sees the same inference."""
    cfg = (thresholds or default_thresholds()).get("champion_fallback") or {}
    if not cfg.get("enabled") or any(c.role == "champion" for c in snap.contacts):
        return snap
    from dataclasses import replace
    from datetime import timedelta

    since = snap.as_of - timedelta(days=cfg["lookback_days"])
    people = {c.email: c for c in snap.contacts if c.status == "active" and c.role != "economic_buyer"}
    counts: dict[str, int] = {}
    last: dict[str, datetime] = {}
    for a in snap.activities:
        if not (since <= a.occurred_at <= snap.as_of):
            continue
        if a.type == "email" and a.direction == "inbound" and not a.extra.get("auto_reply"):
            who = [a.from_email] if a.from_email in people else []
        elif a.type == "meeting" and not a.cancelled:
            who = [e for e in a.participant_emails if e in people]
        else:
            continue
        for e in who:
            counts[e] = counts.get(e, 0) + 1
            last[e] = max(last.get(e, a.occurred_at), a.occurred_at)
    ranked = sorted(counts, key=lambda e: (counts[e], last[e]), reverse=True)
    if not ranked or counts[ranked[0]] < cfg["min_interactions"]:
        return snap
    chosen = ranked[0]
    contacts = tuple(
        replace(c, role="champion", role_inferred=True) if c.email == chosen else c for c in snap.contacts
    )
    return replace(snap, contacts=contacts)


def run_signals(snap: Snapshot, thresholds: dict[str, Any] | None = None) -> list[SignalHit]:
    """All registered signals over one snapshot, in registry order."""
    from casefile.signals import crm, engagement, language, usage  # noqa: F401  (registers the signals)

    cfg = thresholds or default_thresholds()
    snap = apply_role_fallbacks(snap, cfg)
    hits: list[SignalHit] = []
    for name, spec in REGISTRY.items():
        if cfg.get(name, {}).get("enabled", True):
            hits.extend(spec.fn(snap, cfg.get(name, {})))
    return hits


# ---------------------------------------------------------------- helpers shared by signals


def iso(d: date | datetime | None) -> str | None:
    return d.isoformat() if d else None


def day_label(d: date) -> str:
    return d.strftime("%d %b %Y")
