"""Phase 3 evaluations. Quality numbers on synthetic data are labelled "simulated"; money spent is
"observed" (it is read back from the llm_call log)."""

from __future__ import annotations

import json
import random
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.ai.email_reader import ACCEPT_AT, EmailReading, LabelOut, email_content
from casefile.ai.gateway import AiGateway, load_prompt
from casefile.ai.injection import RULES_VERSION, detect
from casefile.ai.masking import mask
from casefile.config import REPO_ROOT
from casefile.db.models import Activity, ActivityType, Direction, EvalRun, EvidenceLabel, Org
from casefile.ingest.text import strip_zero_width

SUITE = REPO_ROOT / "evals" / "datasets" / "injection_suite.jsonl"
LABELS = [
    "complaint",
    "frustration",
    "competitor_mention",
    "upgrade_interest",
    "pricing_question",
    "seat_request",
]
# Keyword baseline: what a simple rules-only reader would do. Generic words only (no competitor names).
KEYWORDS = {
    "complaint": (
        r"\b(errors?|timed out|time out|not working|broken|issues?|problems?|bugs?|missing data|fix(ed)?)\b"
    ),
    "frustration": r"\b(frustrat\w*|annoy\w*|still waiting|unacceptable|honestly)\b",
    "competitor_mention": r"\b(evaluat\w+|competitors?|alternatives?|switch(ing)? to)\b",
    "upgrade_interest": r"\b(upgrade|enterprise tier|higher tier|more seats|add\w* seats|expand)\b",
    "pricing_question": r"\b(price|pricing|cost|quote|tier)\b",
    "seat_request": r"\b(add\w*|more|extra)\s+(\d+\s+)?seats?\b",
}


def _prf(tp: int, fp: int, fn: int) -> dict[str, float | None]:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    return {
        "precision": round(p, 3) if p is not None else None,
        "recall": round(r, 3) if r is not None else None,
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def _record(session: Session, org: Org, suite: str, metrics: dict[str, Any]) -> dict[str, Any]:
    session.add(EvalRun(org_id=org.id, suite=suite, metrics=metrics, label=EvidenceLabel.simulated))
    return metrics


# ---------------------------------------------------------------- injection


def eval_injection(
    session: Session, org: Org, gateway: AiGateway | None, *, ai_benign: int = 10
) -> dict[str, Any]:
    """Rules on the whole suite (free); the AI reader on every attack plus ``ai_benign`` lookalikes."""
    rows = [json.loads(line) for line in SUITE.read_text().splitlines()]
    system, version = load_prompt("email_reader")
    rules = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    union = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    ai_only = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}  # independent of the rule tuning
    spent = 0.0
    ai_seen = 0
    benign_sent = 0
    missed: list[str] = []
    for row in rows:
        body, zw = strip_zero_width(row["body"])
        rule_hit = (
            detect(f"{row['subject']}\n{body}", row["hidden_text"], zw or row["zero_width"]) is not None
        )
        key = ("tp" if rule_hit else "fn") if row["attack"] else ("fp" if rule_hit else "tn")
        rules[key] += 1
        ai_hit = False
        if gateway is not None and (row["attack"] or benign_sent < ai_benign):
            benign_sent += not row["attack"]
            content = "\n".join(
                [
                    f'<customer_email id="{row["id"]}" untrusted="true">',
                    f"Subject: {row['subject']}",
                    "From: customer contact",
                    "",
                    row["body"],
                    "</customer_email>",
                ]
                + (
                    ['<hidden_text untrusted="true">', row["hidden_text"], "</hidden_text>"]
                    if row["hidden_text"]
                    else []
                )
            )
            r = gateway.structured(
                purpose="eval_injection",
                role="reader",
                system=system,
                content=content,
                schema=EmailReading,
                prompt_version=version,
            )
            spent += r.cost_usd
            if r.ok and r.output is not None:
                ai_seen += 1
                ai_hit = r.output.injection.suspected
                ai_key = ("tp" if ai_hit else "fn") if row["attack"] else ("fp" if ai_hit else "tn")
                ai_only[ai_key] += 1
        both = rule_hit or ai_hit
        key = ("tp" if both else "fn") if row["attack"] else ("fp" if both else "tn")
        union[key] += 1
        if row["attack"] and not both:
            missed.append(row["id"])
    metrics = {
        "rules_version": RULES_VERSION,
        "rules_only": {**rules, "note": "rules were tuned on this suite: in-sample"},
        "rules_plus_ai": {**union, "ai_calls_scored": ai_seen},
        "ai_only": {**ai_only, "note": "out-of-sample for the AI: the prompt was not tuned on this suite"},
        "missed_by_both": missed,
        "cost_usd_observed": round(spent, 5),
    }
    return _record(session, org, "injection", metrics)


# ---------------------------------------------------------------- email labels


def _accepted(labels: list[LabelOut]) -> set[str]:
    return {lab.label for lab in labels if lab.confidence >= ACCEPT_AT}


def eval_email_labels(
    session: Session,
    org: Org,
    gateway: AiGateway,
    truth: dict[str, Any],
    *,
    per_label: int = 5,
    unlabelled: int = 10,
    seed: int = 1,
) -> dict[str, Any]:
    """Small stratified sample of human inbound emails (cost control): up to ``per_label`` per label plus
    ``unlabelled`` with no label. AI reader vs the keyword baseline, per label."""
    rng = random.Random(seed)
    emails = session.scalars(
        select(Activity).where(
            Activity.org_id == org.id,
            Activity.type == ActivityType.email,
            Activity.direction == Direction.inbound,
            Activity.injection_flag.is_(False),
            Activity.extra["auto_reply"].astext.is_distinct_from("true"),
            Activity.extra["bounce"].astext.is_distinct_from("true"),
        )
    ).all()
    by_label: dict[str, list[Activity]] = defaultdict(list)
    for e in emails:
        labels = set((truth["emails"].get(e.source_id) or {}).get("labels", [])) & set(LABELS)
        for lab in labels or {"none"}:
            by_label[lab].append(e)
    sample: dict[Any, Activity] = {}
    for lab in [*LABELS, "none"]:
        pool = by_label.get(lab, [])
        for e in rng.sample(pool, k=min(len(pool), unlabelled if lab == "none" else per_label)):
            sample[e.id] = e
    system, version = load_prompt("email_reader")
    ai_counts = {lab: {"tp": 0, "fp": 0, "fn": 0} for lab in LABELS}
    kw_counts = {lab: {"tp": 0, "fp": 0, "fn": 0} for lab in LABELS}
    unverified = 0
    scored = 0
    spent = 0.0
    for e in sample.values():
        gold = set((truth["emails"].get(e.source_id) or {}).get("labels", [])) & set(LABELS)
        content = email_content(e, "customer contact")
        r = gateway.structured(
            purpose="eval_email_labels",
            role="reader",
            system=system,
            content=content,
            schema=EmailReading,
            prompt_version=version,
        )
        if not r.ok or r.output is None:
            continue
        scored += 1
        sent = " ".join(mask(content).split())
        verified = [
            lab for lab in r.output.labels if lab.quote.strip() and " ".join(lab.quote.split()) in sent
        ]
        unverified += len(r.output.labels) - len(verified)
        predicted = _accepted(verified)
        text = f"{e.subject or ''} {e.body_text or ''}"
        keyword = {lab for lab, pat in KEYWORDS.items() if re.search(pat, text, re.I)}
        for lab in LABELS:
            for counts, pred in ((ai_counts, predicted), (kw_counts, keyword)):
                if lab in pred and lab in gold:
                    counts[lab]["tp"] += 1
                elif lab in pred:
                    counts[lab]["fp"] += 1
                elif lab in gold:
                    counts[lab]["fn"] += 1

    def total(counts: dict[str, dict[str, int]]) -> dict[str, float | None]:
        return _prf(*(sum(c[k] for c in counts.values()) for k in ("tp", "fp", "fn")))

    metrics = {
        "sample_size": len(sample),
        "scored": scored,
        "ai_reader": {"overall": total(ai_counts), "by_label": {k: _prf(**v) for k, v in ai_counts.items()}},
        "keyword_baseline": {
            "overall": total(kw_counts),
            "by_label": {k: _prf(**v) for k, v in kw_counts.items()},
        },
        "ai_labels_dropped_unverified_quote": unverified,
        "cost_usd_observed": round(spent, 5),
        "cost_note": "0 when every call was answered from the cache",
        "note": "synthetic emails with template-based truth labels; small sample for cost control",
    }
    return _record(session, org, "email_labels", metrics)


def write_results(name: str, metrics: dict[str, Any]) -> Path:
    out = REPO_ROOT / "evals" / "results" / f"{name}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(metrics, indent=2, default=str))
    return out
