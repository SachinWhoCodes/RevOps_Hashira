"""Case writer (Claude Opus 5.5) with a code checker in front of every sentence.

Flow: build the evidence pack → ask for structured claims → check every claim in code →
if anything fails, ask once more with the reasons → show only claims that pass. If no claim
survives (or AI is off, over budget, or refused), show the deterministic template built from the
signal summaries — it cites its evidence and passes the same checker. The case shows which one it is.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from casefile.ai.checker import CheckReport, check_explanation
from casefile.ai.evidence import EvidencePack, build_pack, render_for_writer
from casefile.ai.gateway import AiGateway, load_prompt
from casefile.db.models import Polarity
from casefile.signals.base import SignalHit
from casefile.timeline.snapshot import Snapshot

MAX_ATTEMPTS = 2


class ClaimOut(BaseModel):
    text: str
    evidence_ids: list[str]
    fact_keys: list[str]


class ExplanationOut(BaseModel):
    headline: str
    claims: list[ClaimOut]
    blind_spot_note: str


@dataclass
class Written:
    headline: str
    claims: list[dict[str, Any]]  # {text, evidence: [{eid, kind, id, label, at}], fact_keys}
    blind_spot_note: str
    status: str  # verified | fallback
    report: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    prompt_version: str | None = None


def template_headline(risk: int, opportunity: int) -> str:
    if risk >= 40:
        return "Renewal at risk"
    if opportunity >= 40:
        return "Expansion opportunity"
    return "Signals to review"


def template_claims(hits: list[SignalHit], pack: EvidencePack) -> list[dict[str, Any]]:
    order = {Polarity.risk: 0, Polarity.urgency: 1, Polarity.opportunity: 2, Polarity.positive: 3}
    claims = []
    for ns, hit in sorted(zip(pack.signal_keys, hits, strict=True), key=lambda p: order[p[1].polarity]):
        claims.append(
            {
                "text": hit.summary,
                "evidence_ids": pack.signal_eids[ns],
                "fact_keys": [k for k in pack.facts if k.startswith(f"{ns}.")],
            }
        )
    return claims


def _publish(claims: list[dict[str, Any]], pack: EvidencePack) -> list[dict[str, Any]]:
    return [
        {
            "text": c["text"],
            "evidence": pack.item_refs(list(c["evidence_ids"])),
            "fact_keys": list(c["fact_keys"]),
        }
        for c in claims
    ]


def template_explanation(
    hits: list[SignalHit], pack: EvidencePack, *, risk: int, opportunity: int, reason: str
) -> Written:
    claims = template_claims(hits, pack)
    report = check_explanation(template_headline(risk, opportunity), claims, "", pack)
    shown = [c for c, v in zip(claims, [*report.passed, *report.failed], strict=False) if v.ok]
    # Templates are built from the same facts the checker uses, so they should always pass; if one
    # ever does not, it is dropped like any other claim and the failure is recorded.
    passed_texts = {v.text for v in report.passed}
    shown = [c for c in claims if c["text"] in passed_texts]
    return Written(
        headline=template_headline(risk, opportunity),
        claims=_publish(shown, pack),
        blind_spot_note="",
        status="fallback",
        report={"reason": reason, "template_check": report.summary()},
    )


def _feedback(report: CheckReport) -> str:
    lines = [
        "",
        "CHECKER FEEDBACK ON YOUR PREVIOUS ANSWER (these were deleted; rewrite all claims within the rules):",
    ]
    for v in report.failed:
        lines.append(f'- "{v.text}": {"; ".join(v.reasons)}')
    if report.headline_reasons:
        lines.append(f"- headline: {'; '.join(report.headline_reasons)}")
    if report.note_reasons:
        lines.append(f"- blind_spot_note: {'; '.join(report.note_reasons)}")
    return "\n".join(lines)


def write_explanation(
    gateway: AiGateway,
    snap: Snapshot,
    hits: list[SignalHit],
    *,
    risk: int,
    opportunity: int,
    confidence: str,
    blind_spots: list[dict[str, Any]],
    points: dict[str, int],
    owner_name: str | None,
    account_id: uuid.UUID | None = None,
) -> Written:
    pack = build_pack(snap, hits, owner_name=owner_name, risk=risk, opportunity=opportunity)
    if not hits:
        return Written(template_headline(risk, opportunity), [], "", "fallback", {"reason": "no signals"})
    system, version = load_prompt("case_writer")
    content = render_for_writer(
        pack, snap, hits, owner_name=owner_name, confidence=confidence, blind_spots=blind_spots, points=points
    )
    attempts: list[dict[str, Any]] = []
    best: tuple[ExplanationOut, CheckReport] | None = None
    feedback = ""
    model = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        result = gateway.structured(
            purpose="case_writer",
            role="writer",
            system=system,
            content=content + feedback,
            schema=ExplanationOut,
            prompt_version=version,
            account_id=account_id,
        )
        model = result.model or model
        if not result.ok or result.output is None:
            attempts.append({"attempt": attempt, "error": result.error, "cost_usd": result.cost_usd})
            break  # API failures, refusals and budget stops are not retried here
        out = result.output
        claims = [c.model_dump() for c in out.claims]
        report = check_explanation(out.headline, claims, out.blind_spot_note, pack)
        attempts.append(
            {"attempt": attempt, "cached": result.cached, "cost_usd": result.cost_usd, **report.summary()}
        )
        if best is None or len(report.passed) > len(best[1].passed):
            best = (out, report)
        if report.all_ok:
            break
        feedback = _feedback(report)

    if best is None or not best[1].passed:
        reason = (
            attempts[-1].get("error")
            if attempts and attempts[-1].get("error")
            else "no claim passed the checker"
        )
        written = template_explanation(hits, pack, risk=risk, opportunity=opportunity, reason=str(reason))
        written.report["attempts"] = attempts
        written.model, written.prompt_version = model, version
        return written
    out, report = best
    passed_texts = {v.text for v in report.passed}
    shown = [c.model_dump() for c in out.claims if c.text in passed_texts]
    return Written(
        headline=out.headline if not report.headline_reasons else template_headline(risk, opportunity),
        claims=_publish(shown, pack),
        blind_spot_note=out.blind_spot_note if not report.note_reasons else "",
        status="verified",
        report={
            "attempts": attempts,
            "removed": [{"text": v.text, "reasons": v.reasons} for v in report.failed],
            "headline_replaced": bool(report.headline_reasons),
        },
        model=model,
        prompt_version=version,
    )
