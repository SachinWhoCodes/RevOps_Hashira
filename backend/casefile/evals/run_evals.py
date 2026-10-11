"""One command for every evaluation suite (plan Phase 5 item 6).

    uv run casefile evals            # every suite that costs nothing
    uv run casefile evals --ai       # also the AI suites (email labels, AI injection reader); costs money

Each suite records an ``eval_run`` row and a line in a markdown table, with its evidence label:
*observed* (measured on real tools or real behaviour of our code), *simulated* (synthetic company),
*estimated*. Test-backed suites run the named pytest files against the test database and report
pass/fail counts — the tests themselves are the specification (e.g. the 20 planted false claims).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from casefile.config import REPO_ROOT, get_settings
from casefile.db.models import EvalRun, EvidenceLabel, Org

BACKEND = REPO_ROOT / "backend"

TEST_SUITES: dict[str, tuple[str, list[str]]] = {
    "planted_false_claims": (
        "AI claim checker removes every planted false claim",
        ["tests/test_ai_checker.py"],
    ),
    "executor_safety": (
        "approve twice, crash/reconcile, conflict, tamper, unapproved, audit failure, injection end to end",
        ["tests/test_actions.py", "tests/test_writers.py", "tests/test_db_role.py", "tests/test_drafter.py"],
    ),
    "time_travel_leakage": (
        "as-of snapshots never contain the future (property test + canary records)",
        ["tests/test_snapshot_leakage.py", "tests/test_backtest.py"],
    ),
    "signals_and_scoring": (
        "each signal fires on its pattern and not on look-alikes",
        ["tests/test_signals.py", "tests/test_scoring.py"],
    ),
}


@dataclass
class SuiteResult:
    suite: str
    label: EvidenceLabel
    headline: str
    metrics: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    recorded: bool = False  # the suite already wrote its own eval_run row


def _pytest(files: list[str]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "junit.xml"
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={report}", *files],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            check=False,
        )
        root = ET.parse(report).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    total = sum(int(s.get("tests", 0)) for s in suites)
    failed = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    return {
        "tests": total,
        "passed": total - failed - skipped,
        "failed": failed,
        "skipped": skipped,
        "exit": proc.returncode,
    }


def run_test_suites() -> list[SuiteResult]:
    out = []
    for name, (what, files) in TEST_SUITES.items():
        m = _pytest(files)
        out.append(
            SuiteResult(
                name,
                EvidenceLabel.observed,
                f"{m['passed']}/{m['tests']} pass — {what}",
                {**m, "files": files},
                ok=m["failed"] == 0 and m["tests"] > 0,
            )
        )
    return out


def run_data_suites(session: Session, org: Org) -> list[SuiteResult]:
    from casefile.backtest.outcomes import derive_outcomes
    from casefile.backtest.runner import run_backtest
    from casefile.evals.ai_evals import eval_injection
    from casefile.identity.evaluate import evaluate_matching
    from casefile.scoring.evaluate import evaluate_ranking

    out = []
    truth_path = get_settings().synth_dir / "default" / "ground_truth.json"
    truth = json.loads(truth_path.read_text()) if truth_path.exists() else None
    if truth is not None:
        m = evaluate_matching(session, org, truth)
        out.append(
            SuiteResult(
                "identity_matching",
                EvidenceLabel.simulated,
                f"{m['correct']}/{m['matched']} matched emails on the right account; "
                f"coverage {m['coverage']}",
                m,
            )
        )
        r = evaluate_ranking(session, org, truth)
        out.append(
            SuiteResult(
                "ranking_vs_planted_truth",
                EvidenceLabel.simulated,
                f"precision@10 {r.get('precision_at_10')}, AUC {r.get('auc_risk_score')} on today's queue "
                f"({r.get('at_risk_started')} planted at-risk accounts with visible signs)",
                r,
            )
        )
    inj = eval_injection(session, org, None)
    rules = inj["rules_only"]
    out.append(
        SuiteResult(
            "injection_rules",
            EvidenceLabel.simulated,
            f"rules caught {rules['tp']}/{rules['tp'] + rules['fn']} attacks, "
            f"{rules['fp']}/{rules['fp'] + rules['tn']} false alarms "
            "(in-sample: the rules were tuned on this suite)",
            inj,
            recorded=True,
        )
    )
    derive_outcomes(session, org)
    run = run_backtest(session, org)
    bm = run.metrics
    h30 = bm["horizon"]["30"]["held_out"]
    out.append(
        SuiteResult(
            "backtest",
            run.label,
            (
                f"held-out AUC at 30 days {h30.get('auc', {}).get('value')} (n={h30.get('n')}); "
                f"weekly precision@10 {bm['rolling']['precision_at_10']['value']} vs "
                f"{bm['rolling']['baseline_nearest_renewal_first']['value']} for nearest-renewal-first; "
                f"{bm['lead_time']['flagged_before_outcome']['value']} of lost renewals flagged ahead"
            ),
            {"run_id": str(run.id), **bm},
        )
    )
    return out


def run_ai_suites(session: Session, org: Org) -> list[SuiteResult]:
    from casefile.ai.gateway import AiGateway
    from casefile.evals.ai_evals import eval_email_labels, eval_injection

    gateway = AiGateway(session, org.id)
    out = []
    inj = eval_injection(session, org, gateway)
    ai_only = inj["ai_only"]
    out.append(
        SuiteResult(
            "injection_ai",
            EvidenceLabel.simulated,
            f"AI reader caught {ai_only['tp']}/{ai_only['tp'] + ai_only['fn']} attacks, "
            f"{ai_only['fp']} false alarms; cost ${inj['cost_usd_observed']}",
            inj,
            recorded=True,
        )
    )
    truth_path = get_settings().synth_dir / "default" / "ground_truth.json"
    if truth_path.exists():
        lab = eval_email_labels(session, org, gateway, json.loads(truth_path.read_text()))
        out.append(
            SuiteResult(
                "email_labels",
                EvidenceLabel.simulated,
                "AI email labels vs keyword baseline (see metrics)",
                lab,
                recorded=True,
            )
        )
    return out


def to_markdown(results: list[SuiteResult], *, ai: bool) -> str:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Casefile evaluation results — {stamp}",
        "",
        "Generated by `uv run casefile evals --out ../docs/RESULTS.md` (run in `backend/`); "
        "do not edit by hand.",
        "",
        "Labels: **observed** = measured on our code or real tools; **simulated** = synthetic company "
        "(rules and data generator written by the same team); **estimated** = modelled.",
        "",
        "| Suite | Label | Result | Status |",
        "|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.suite.replace('_', ' ')} | {r.label.value} | {r.headline} | {'pass' if r.ok else 'FAIL'} |"
        )
    if not ai:
        lines += [
            "",
            "AI suites (email labels, AI injection reader) were not run; use `casefile evals --ai`.",
        ]
    bt = next((r for r in results if r.suite == "backtest"), None)
    if bt is not None:
        from casefile.backtest.report import backtest_markdown

        lines += ["", backtest_markdown(bt.metrics, label=bt.label.value)]
    return "\n".join(lines) + "\n"


def run_all(
    session: Session, org: Org, *, ai: bool = False, tests: bool = True
) -> tuple[list[SuiteResult], str]:
    results: list[SuiteResult] = []
    if tests:
        results += run_test_suites()
    results += run_data_suites(session, org)
    if ai:
        results += run_ai_suites(session, org)
    for r in results:
        if not r.recorded:
            session.add(EvalRun(org_id=org.id, suite=r.suite, metrics=r.metrics, label=r.label))
    session.flush()
    return results, to_markdown(results, ai=ai)
