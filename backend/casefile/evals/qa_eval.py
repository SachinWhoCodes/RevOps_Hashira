"""Phase 6.4 exit test: scripted questions about accounts, answered with verified citations.

A question passes when the answer has at least one sentence that survived the claim checker (or,
when the data genuinely doesn't hold the answer, a checked statement of what is missing) and no
error. Also reported: sentences removed by the checker, tools used, turns and the observed cost.
Answers are cached, so re-running on unchanged accounts costs nothing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.ai.gateway import AiGateway
from casefile.ai.qa import ask
from casefile.cases.service import RecomputeContext, assess
from casefile.config import REPO_ROOT
from casefile.db.models import Account, EvalRun, EvidenceLabel, Org
from casefile.timeline.snapshot import AccountHistory

DATASET = REPO_ROOT / "evals" / "datasets" / "account_qa.jsonl"


def run_qa_eval(session: Session, org: Org, gateway: AiGateway, path: Path = DATASET) -> dict[str, Any]:
    ctx = RecomputeContext.build(session, org)
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        q = json.loads(line)
        acc = session.scalar(select(Account).where(Account.org_id == org.id, Account.name == q["account"]))
        snap = AccountHistory.load(session, acc.id).as_of(datetime.now(UTC)) if acc else None
        if acc is None or snap is None:
            rows.append({**q, "passed": False, "error": "account not found"})
            continue
        r = ask(session, gateway, snap, assess(snap, arr_fallback=ctx.arr_median), q["question"])
        session.commit()
        rows.append(
            {
                **q,
                "passed": r.ok and r.error is None,
                "verified": len(r.sentences),
                "removed": len(r.removed),
                "removed_reasons": [x["reasons"] for x in r.removed],
                "gaps": bool(r.gaps),
                "tools": r.tools_used,
                "turns": r.iterations,
                "cost_usd": round(r.cost_usd, 5),
                "cached": r.cached,
                "error": r.error,
                "answer": [s["text"] for s in r.sentences],
            }
        )
    passed = sum(r["passed"] for r in rows)
    verified = sum(r.get("verified", 0) for r in rows)
    removed = sum(r.get("removed", 0) for r in rows)
    metrics = {
        "questions": len(rows),
        "passed": passed,
        "sentences_verified": verified,
        "sentences_removed": removed,
        "cost_usd_observed": round(sum(r.get("cost_usd", 0) for r in rows), 5),
        "rows": rows,
    }
    session.add(EvalRun(org_id=org.id, suite="account_qa", label=EvidenceLabel.simulated, metrics=metrics))
    session.commit()
    return metrics
