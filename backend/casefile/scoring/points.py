"""Transparent points scoring. Every score carries a breakdown a person can read line by line."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from casefile.signals.base import SignalHit
from casefile.timeline.snapshot import Snapshot

SOURCE_GROUPS = {"crm": {"crm"}, "email": {"gmail"}, "calendar": {"gcal"}, "usage": {"usage"}}


@lru_cache
def default_scoring() -> dict[str, Any]:
    return yaml.safe_load((Path(__file__).parent / "scoring.yaml").read_text())


@dataclass(frozen=True)
class Score:
    risk: int
    opportunity: int
    confidence: str
    priority: float
    opportunity_priority: float
    breakdown: dict[str, Any]
    signature: str


def urgency_factor(days_to_renewal: int | None, cfg: dict[str, Any]) -> float:
    if days_to_renewal is None:
        return float(cfg["urgency_unknown"])
    if days_to_renewal < 0:
        return float(cfg["urgency_overdue"])
    for row in cfg["urgency"]:
        if days_to_renewal <= row["max_days"]:
            return float(row["factor"])
    return float(cfg["urgency"][-1]["factor"])


def confidence(snap: Snapshot, cfg: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    present = [g for g in cfg["confidence_sources"] if SOURCE_GROUPS[g] & snap.sources]
    missing = [g for g in cfg["confidence_sources"] if g not in present]
    levels = cfg["confidence_levels"]
    level = (
        "high" if len(present) >= levels["high"] else "medium" if len(present) >= levels["medium"] else "low"
    )
    return level, present, missing


def signature(hits: list[SignalHit]) -> str:
    """Changes when the set of signals or their evidence changes — not when a day count ticks up.
    Used to decide when to create a new assessment (and, in Phase 3, when to re-run the AI)."""
    parts = sorted(f"{h.type}:{','.join(sorted(e.id for e in h.evidence))}" for h in hits)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def score(
    snap: Snapshot,
    hits: list[SignalHit],
    cfg: dict[str, Any] | None = None,
    *,
    arr_fallback: Decimal | None = None,
) -> Score:
    cfg = cfg or default_scoring()
    risk_lines, positive_lines, opp_lines = [], [], []
    seen: set[str] = set()
    for h in hits:
        if h.type in seen:
            continue  # several hits of one type (e.g. two champions) count once
        seen.add(h.type)
        if h.type in cfg["risk_points"]:
            risk_lines.append({"signal": h.type, "points": cfg["risk_points"][h.type], "summary": h.summary})
        elif h.type in cfg["positive_points"]:
            positive_lines.append(
                {"signal": h.type, "points": -cfg["positive_points"][h.type], "summary": h.summary}
            )
        elif h.type in cfg["opportunity_points"]:
            opp_lines.append(
                {"signal": h.type, "points": cfg["opportunity_points"][h.type], "summary": h.summary}
            )
    raw_risk = sum(line["points"] for line in risk_lines) + sum(line["points"] for line in positive_lines)
    risk = max(0, min(100, raw_risk))
    opportunity = max(0, min(100, sum(line["points"] for line in opp_lines)))

    days = snap.days_to_renewal()
    urgency = urgency_factor(days, cfg)
    arr = snap.account.arr
    arr_assumed = arr is None and arr_fallback is not None
    arr_value = float(arr if arr is not None else (arr_fallback or 0))
    priority = round(risk / 100 * arr_value * urgency, 2)
    opportunity_priority = round(opportunity / 100 * arr_value, 2)
    level, present, missing = confidence(snap, cfg)
    breakdown = {
        "risk": {"lines": risk_lines + positive_lines, "raw": raw_risk, "score": risk},
        "opportunity": {"lines": opp_lines, "score": opportunity},
        "urgency": {"days_to_renewal": days, "factor": urgency},
        "arr": {"value": arr_value, "assumed": arr_assumed},
        "priority": priority,
        "opportunity_priority": opportunity_priority,
        "confidence": {"level": level, "sources_present": present, "sources_missing": missing},
        "scoring_version": cfg["version"],
    }
    return Score(risk, opportunity, level, priority, opportunity_priority, breakdown, signature(hits))
