"""Metrics with confidence ranges. Every number carries its n; nothing is reported without it.

Ranges: proportions (precision, recall, shares) use the Wilson score interval, which stays honest for
small n; AUC and averages use a seeded percentile bootstrap. With few positives the ranges are wide
on purpose — that is the honest answer.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Callable, Sequence
from typing import Any

BOOTSTRAP_SAMPLES = 1000
SEED = 20261011


def auc(scores: Sequence[float], labels: Sequence[bool]) -> float | None:
    pos = [s for s, y in zip(scores, labels, strict=True) if y]
    neg = [s for s, y in zip(scores, labels, strict=True) if not y]
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def precision_recall(flags: Sequence[bool], labels: Sequence[bool]) -> tuple[float | None, float | None]:
    tp = sum(1 for f, y in zip(flags, labels, strict=True) if f and y)
    fp = sum(1 for f, y in zip(flags, labels, strict=True) if f and not y)
    fn = sum(1 for f, y in zip(flags, labels, strict=True) if not f and y)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return precision, recall


def precision_at_k(scores: Sequence[float], labels: Sequence[bool], k: int) -> float | None:
    if not scores:
        return None
    ranked = sorted(zip(scores, labels, strict=True), key=lambda p: p[0], reverse=True)[:k]
    return sum(1 for _, y in ranked if y) / len(ranked)


def bootstrap(
    rows: Sequence[Any], stat: Callable[[Sequence[Any]], float | None], *, samples: int = BOOTSTRAP_SAMPLES
) -> tuple[float, float] | None:
    """95% percentile interval of ``stat`` over resamples of ``rows`` (None if it cannot be computed)."""
    if len(rows) < 2:
        return None
    rng = random.Random(SEED)
    values = []
    for _ in range(samples):
        sample = [rows[rng.randrange(len(rows))] for _ in rows]
        v = stat(sample)
        if v is not None:
            values.append(v)
    if len(values) < samples * 0.5:
        return None
    values.sort()
    return values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]


def block_bootstrap(
    values: Sequence[float], *, block: int = 13, samples: int = BOOTSTRAP_SAMPLES
) -> tuple[float, float] | None:
    """95% range of the mean of a weekly series whose neighbours overlap (each week looks 90 days
    ahead). Resampling whole 13-week blocks keeps the overlap inside a block, so the range is not
    falsely narrow."""
    blocks = [list(values[i : i + block]) for i in range(0, len(values), block)]
    if len(blocks) < 2:
        return None
    rng = random.Random(SEED)
    means = []
    for _ in range(samples):
        picked = [v for _ in blocks for v in blocks[rng.randrange(len(blocks))]]
        means.append(statistics.fmean(picked))
    means.sort()
    return means[int(0.025 * (len(means) - 1))], means[int(0.975 * (len(means) - 1))]


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson score interval for a proportion. Unlike a bootstrap it stays honest for small n:
    4 out of 4 gives roughly 0.51-1.00, not 1.00-1.00."""
    if n == 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def metric(value: float | None, ci: tuple[float, float] | None, n: int, **extra: Any) -> dict[str, Any]:
    return {
        "value": None if value is None else round(value, 3),
        "ci95": None if ci is None else [round(ci[0], 3), round(ci[1], 3)],
        "n": n,
        **extra,
    }


def median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None
