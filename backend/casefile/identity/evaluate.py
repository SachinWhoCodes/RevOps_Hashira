"""Measure identity resolution against a ground truth (synthetic now, hand-checked real later)."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.db.models import Account, Activity, MatchMethod, Org, UsageDaily


def evaluate_matching(
    session: Session, org: Org, truth: dict[str, Any], label: str = "simulated"
) -> dict[str, Any]:
    crm_to_id = {
        crm: acc_id
        for crm, acc_id in session.execute(select(Account.crm_id, Account.id).where(Account.org_id == org.id))
    }
    emails = truth["emails"]
    rows = session.execute(
        select(Activity.source_id, Activity.account_id, Activity.match_method).where(
            Activity.org_id == org.id, Activity.source == "gmail"
        )
    ).all()
    by_method: dict[str, Counter[str]] = defaultdict(Counter)
    totals: Counter[str] = Counter()
    for source_id, account_id, method in rows:
        expected_crm = (emails.get(source_id) or {}).get("account")
        if expected_crm is None:
            continue
        expected = crm_to_id.get(expected_crm)
        totals["with_truth"] += 1
        if method == MatchMethod.internal_only:
            totals["internal_only"] += 1
            continue
        if account_id is None:
            outcome = "ambiguous" if method == MatchMethod.ambiguous else "unmatched"
        else:
            outcome = "correct" if account_id == expected else "wrong"
        totals[outcome] += 1
        by_method[method.value][outcome] += 1

    matched = totals["correct"] + totals["wrong"]
    usage_truth: dict[str, str] = truth.get("usage_keys", {})
    usage_rows = session.execute(
        select(UsageDaily.external_account_key, UsageDaily.account_id)
        .where(UsageDaily.org_id == org.id)
        .distinct()
    ).all()
    usage: Counter[str] = Counter()
    for key, account_id in usage_rows:
        expected = crm_to_id.get(usage_truth.get(key, ""))
        if account_id is None:
            usage["unmatched"] += 1
        else:
            usage["correct" if account_id == expected else "wrong"] += 1
    return {
        "label": label,
        "emails_with_truth": totals["with_truth"],
        "matched": matched,
        "correct": totals["correct"],
        "wrong": totals["wrong"],
        "unmatched": totals["unmatched"],
        "ambiguous": totals["ambiguous"],
        "precision_of_matched": round(totals["correct"] / matched, 4) if matched else None,
        "coverage": round(matched / totals["with_truth"], 4) if totals["with_truth"] else None,
        "by_method": {m: dict(c) for m, c in sorted(by_method.items())},
        "usage_keys": dict(usage),
    }
