"""How well does today's ranking surface the accounts that really are at risk? (synthetic truth)

Fair truth: an account counts as at risk only if its planted decline started at least
``min_days_observable`` days ago — every signal needs some time to show (a 10-day silence, a
21-day meeting gap, a 28-day usage window). Planted declines that start later, and deliberate
"surprise churns" with no warning signs, are reported separately — no system could flag them
today, so they are not misses.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.db.models import Account, Assessment, Org

AT_RISK = {"silent_churn", "usage_cliff", "champion_departure", "downgrade_usage"}


def _auc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def evaluate_ranking(
    session: Session, org: Org, truth: dict[str, Any], *, flag_at: int = 40, min_days_observable: int = 14
) -> dict[str, Any]:
    today = date.fromisoformat(truth["today"])
    crm_ids = dict(session.execute(select(Account.id, Account.crm_id).where(Account.org_id == org.id)).all())
    latest: dict[Any, Assessment] = {}
    for a in session.scalars(
        select(Assessment).where(Assessment.org_id == org.id).order_by(Assessment.created_at)
    ):
        latest[a.account_id] = a  # last one wins

    rows = []
    for acc_id, a in latest.items():
        t = truth["accounts"].get(crm_ids.get(acc_id, ""))
        if t is None or t["churned_on"]:
            continue
        current = t["periods"][-1]
        onset = date.fromisoformat(current["onset"]) if current["onset"] else None
        story = current["story"]
        started = story in AT_RISK and onset is not None and (today - onset).days >= min_days_observable
        rows.append(
            {
                "story": story,
                "risk": a.risk_score,
                "priority": a.priority,
                "positive": started,
                "future_onset": story in AT_RISK and not started,
            }
        )
    rows.sort(key=lambda r: (r["risk"], r["priority"]), reverse=True)
    positives = [r for r in rows if r["positive"]]
    k = len(positives)

    def precision_at(n: int) -> float | None:
        top = rows[:n]
        return round(sum(r["positive"] for r in top) / len(top), 3) if top else None

    flagged = [r for r in rows if r["risk"] >= flag_at]
    return {
        "label": "simulated",
        "accounts_ranked": len(rows),
        "at_risk_started": k,
        "min_days_observable": min_days_observable,
        "planted_not_yet_observable": sum(r["future_onset"] for r in rows),
        "surprise_churns_undetectable_by_design": sum(r["story"] == "surprise_churn" for r in rows),
        "precision_at_10": precision_at(10),
        "precision_at_20": precision_at(20),
        "recall_at_k": round(sum(r["positive"] for r in rows[:k]) / k, 3) if k else None,
        "auc_risk_score": _auc(
            [r["risk"] for r in positives], [r["risk"] for r in rows if not r["positive"]]
        ),
        "flagged_at_threshold": {
            "threshold": flag_at,
            "flagged": len(flagged),
            "precision": round(sum(r["positive"] for r in flagged) / len(flagged), 3) if flagged else None,
            "recall": round(sum(r["positive"] for r in flagged) / k, 3) if k else None,
        },
        "recall_by_story": {
            s: f"{sum(r['risk'] >= flag_at for r in group)}/{len(group)}"
            for s in sorted({r["story"] for r in positives})
            for group in [[r for r in positives if r["story"] == s]]
        },
        "false_flags_by_story": dict(Counter(r["story"] for r in flagged if not r["positive"])),
    }
