"""Markdown for the back-test: every number with its n, 95% range and evidence label."""

from __future__ import annotations

from typing import Any


def _m(m: dict[str, Any] | None, *, pct: bool = False, whole: bool = False) -> str:
    if not m or m.get("value") is None:
        return "—"

    def f(v: float) -> str:
        return f"{round(v * 100)}%" if pct else str(round(v)) if whole else f"{v:.2f}"

    ci = m.get("ci95")
    rng = f" ({f(ci[0])} to {f(ci[1])})" if ci else ""
    return f"{f(m['value'])}{rng}, n={m['n']}"


def backtest_markdown(metrics: dict[str, Any], *, label: str) -> str:
    m = metrics
    ft = m["flag_threshold"]
    lines = [
        f"## Back-test (label: {label})",
        "",
        f"- Renewal outcomes from deal history: {', '.join(f'{v} {k}' for k, v in m['outcomes'].items())}. "
        f"Positive = {' or '.join(m['positive_definition'])}.",
        f"- Scored {m['events']['scored']} of {m['events']['total']} renewals "
        f"({'; '.join(f'{v} skipped: {k}' for k, v in m['skipped'].items()) or 'none skipped'}).",
        f"- Tuning half: renewals before {m['split_date']} ({m['events']['tuning']}); held-out half: "
        f"{m['events']['held_out']}. Flag threshold {ft['default']} (live default); "
        f"the tuning half preferred {ft['tuned_on_tuning_period']} ({ft['objective']}).",
        f"- Left out for the whole run: {', '.join(m['excluded_signals']) or 'nothing'} "
        f"(AI email-label coverage of past emails: {round(m['label_coverage'] * 100, 1)}%).",
        "",
        "| Scored | Set | Renewals (lost) | AUC | Precision when flagged | Recall |",
        "|---|---|---|---|---|---|",
    ]
    names = {
        "all": "all",
        "held_out": "held-out",
        "held_out_tuned_threshold": f"held-out, threshold {ft['tuned_on_tuning_period']}",
    }
    for h, blocks in m["horizon"].items():
        for key, name in names.items():
            b = blocks.get(key, {})
            if not b.get("n"):
                continue
            lines.append(
                f"| {h} days before | {name} | {b['n']} ({b['positives']}) | {_m(b['auc'])} | "
                f"{_m(b['precision'], pct=True)} | {_m(b['recall'], pct=True)} |"
            )
        bs = blocks["all"].get("best_single_signal")
        if bs:
            lines.append(
                f"| {h} days before | baseline: best single signal ({bs['signal']}) | {blocks['all']['n']} | "
                f"{_m(bs['auc'])} | {_m(bs['precision'], pct=True)} | {_m(bs['recall'], pct=True)} |"
            )
        lines.append(
            f"| {h} days before | baseline: renewal date alone | {blocks['all']['n']} "
            "| 0.50 (constant) | — | — |"
        )
    r, lead, fa = m["rolling"], m["lead_time"], m["false_alarms"]
    health = m["horizon"][next(iter(m["horizon"]))]["all"].get("health_score")
    lines += [
        "",
        f"Health-score baseline: {health if isinstance(health, str) else _m(health['auc'])}.",
        "",
        "| Rolling (weekly, renewals due in the next 90 days) | Value |",
        "|---|---|",
        f"| Casefile precision@10 | {_m(r['precision_at_10'], pct=True)} weeks |",
        f"| Baseline: nearest renewal first | {_m(r['baseline_nearest_renewal_first'], pct=True)} weeks |",
        f"| Baseline: best single signal | {_m(r['baseline_best_single_signal'], pct=True)} weeks |",
        f"| Lost renewals flagged before the outcome | {_m(lead['flagged_before_outcome'], pct=True)} |",
        f"| Median warning when flagged (days) | {_m(lead['median_lead_days'], whole=True)} |",
        f"| Kept renewals flagged in their last 90 days | {_m(fa['flagged_in_last_90_days'], pct=True)} |",
        "",
    ]
    if m.get("fallback"):
        lines += [f"**{m['fallback']}**", ""]
    return "\n".join(lines)
