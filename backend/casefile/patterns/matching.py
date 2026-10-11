"""Renewal-pattern matching: learn from finished cycles, compare a client's path with them.

The model is a nearest-neighbour model ("these past cycles looked most like this one at the same
distance from their decision"). Training learns, for each stage (days before the decision):

- how much each number told renewals and losses apart in this company's own history beyond what
  chance would give (its weight: ``2 x max(0, |AUC - 0.5| - z x SE)`` on the finished cycles at that
  stage, SE from Hanley & McNeil 1982, z = 2.77 because nine numbers are tried at each stage, so luck
  gets nine chances: a Bonferroni-corrected 95%). A number that never separated them, or did so only as
  much as luck could with this few cycles, counts for nothing; a stage where nothing counts gives no
  verdict at all, and
- how much each number usually varies (its spread), so distances are in comparable units.

Matching compares the *path*, not one day: the client's pattern now and at the two checkpoints
before (30 and 60 days earlier), nearer moments counting more. Pure functions only; no database,
no AI (rule R2: numbers come from code).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from casefile.backtest.metrics import auc, bootstrap, metric, wilson
from casefile.patterns.features import FEATURES, scaled

STEP_DAYS = 30
STAGES: tuple[int, ...] = tuple(range(30, 361, STEP_DAYS))  # 30, 60, ... 360 days before the decision
K = 9  # nearest past cycles shown and counted
PATH_STEPS = 3  # this checkpoint and the two before it
PATH_DECAY = 0.6  # each earlier checkpoint counts 0.6 times the one after it
MIN_COVERAGE = 0.5  # a number missing for more than half the cycles at a stage is not used there
MIN_STAGE_CYCLES = 20  # fewer finished cycles than this at a stage: too few to compare
MIN_STAGE_LOST = 3  # ...or fewer lost ones than this
MIN_TEST_LOST = 10  # the test names a winner only with this many lost renewals (as the back-test)
MISSING_PENALTY = 1.0  # one spread of distance when only one side has a value
CHANCE_Z = 2.77  # 95% two-sided, Bonferroni-corrected for the nine numbers tried at each stage

Features = dict[str, float | None]


@dataclass(frozen=True)
class Cycle:
    """A finished cycle as the engine sees it: its end, whether revenue was lost, and its pattern at
    each stage it was observed (stage -> (moment, features))."""

    key: str
    end: date
    lost: bool
    points: dict[int, tuple[datetime, Features]]
    known: date | None = None  # when the outcome became known; defaults to the end date

    @property
    def known_on(self) -> date:
        return self.known or self.end


@dataclass
class StageModel:
    stage: int
    n: int
    lost: int
    base_rate: float | None
    weights: dict[str, float] = field(default_factory=dict)
    spread: dict[str, float] = field(default_factory=dict)
    means: dict[str, dict[str, float | None]] = field(default_factory=dict)  # kept | lost -> key -> mean

    @property
    def ready(self) -> bool:
        return self.n >= MIN_STAGE_CYCLES and self.lost >= MIN_STAGE_LOST and any(self.weights.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "n": self.n,
            "lost": self.lost,
            "base_rate": None if self.base_rate is None else round(self.base_rate, 4),
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "spread": {k: round(v, 6) for k, v in self.spread.items()},
            "means": self.means,
            "ready": self.ready,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StageModel:
        return cls(
            stage=int(d["stage"]),
            n=int(d["n"]),
            lost=int(d["lost"]),
            base_rate=d.get("base_rate"),
            weights=dict(d.get("weights") or {}),
            spread=dict(d.get("spread") or {}),
            means=dict(d.get("means") or {}),
        )


def _mean(values: Sequence[float]) -> float | None:
    return round(statistics.fmean(values), 4) if values else None


def auc_se(a: float, n_pos: int, n_neg: int) -> float:
    """Standard error of an AUC (Hanley & McNeil 1982)."""
    if not n_pos or not n_neg:
        return 0.5
    q1, q2 = a / (2 - a), 2 * a * a / (1 + a)
    var = (a * (1 - a) + (n_pos - 1) * (q1 - a * a) + (n_neg - 1) * (q2 - a * a)) / (n_pos * n_neg)
    return math.sqrt(max(var, 0.0))


def beyond_chance(a: float, n_pos: int, n_neg: int) -> float:
    """How far an AUC is from a coin flip after allowing for luck (0 when within ``CHANCE_Z``
    standard errors of 0.5), scaled to 0..1."""
    return round(max(0.0, abs(a - 0.5) - CHANCE_Z * auc_se(a, n_pos, n_neg)) * 2, 6)


def learn_stage(stage: int, rows: Sequence[tuple[Features, bool]]) -> StageModel:
    """Weights, spreads and per-outcome averages from the finished cycles observed at one stage."""
    lost = sum(1 for _, y in rows if y)
    model = StageModel(stage, len(rows), lost, lost / len(rows) if rows else None)
    for spec in FEATURES:
        pairs = [(scaled(spec.key, f.get(spec.key)), y) for f, y in rows]
        present = [(v, y) for v, y in pairs if v is not None]
        raw = [(f.get(spec.key), y) for f, y in rows if f.get(spec.key) is not None]
        model.means.setdefault("kept", {})[spec.key] = _mean([v for v, y in raw if not y and v is not None])
        model.means.setdefault("lost", {})[spec.key] = _mean([v for v, y in raw if y and v is not None])
        if not rows or len(present) < MIN_COVERAGE * len(rows):
            continue
        strength = auc([v for v, _ in present], [y for _, y in present])
        n_lost = sum(1 for _, y in present if y)
        model.weights[spec.key] = (
            0.0 if strength is None else beyond_chance(strength, n_lost, len(present) - n_lost)
        )
        model.spread[spec.key] = statistics.pstdev([v for v, _ in present]) or 1.0
    return model


def _point_distance(a: Features, b: Features, model: StageModel) -> float:
    total = 0.0
    for key, w in model.weights.items():
        if not w:
            continue
        va, vb = scaled(key, a.get(key)), scaled(key, b.get(key))
        if va is None and vb is None:
            continue
        if va is None or vb is None:
            total += w * MISSING_PENALTY
        else:
            total += w * ((va - vb) / model.spread[key]) ** 2
    return total


def path_distance(
    mine: dict[int, Features], theirs: dict[int, Features], model: StageModel, *, steps: int = PATH_STEPS
) -> float | None:
    """Distance between two paths ending at ``model.stage`` (None if either lacks that checkpoint)."""
    if model.stage not in mine or model.stage not in theirs:
        return None
    total = weight = 0.0
    for j in range(steps):
        s = model.stage + STEP_DAYS * j
        if s in mine and s in theirs:
            total += PATH_DECAY**j * _point_distance(mine[s], theirs[s], model)
            weight += PATH_DECAY**j
    return math.sqrt(total / weight)


@dataclass(frozen=True)
class Neighbour:
    key: str
    distance: float
    lost: bool
    closer_than: float  # share of the compared cycles that are further away (0..1)


def nearest(
    mine: dict[int, Features],
    library: Sequence[Cycle],
    model: StageModel,
    *,
    k: int = K,
) -> tuple[list[Neighbour], int]:
    """The ``k`` finished cycles whose path to this stage looks most like ``mine``, and how many were
    compared. Ties break on the cycle key, so results are reproducible."""
    scored = []
    for c in library:
        d = path_distance(mine, {s: f for s, (_, f) in c.points.items()}, model)
        if d is not None:
            scored.append((d, c.key, c.lost))
    scored.sort(key=lambda r: (r[0], r[1]))
    n = len(scored)
    out = [
        Neighbour(key, round(d, 4), lost, round((n - i - 1) / (n - 1), 4) if n > 1 else 1.0)
        for i, (d, key, lost) in enumerate(scored[:k])
    ]
    return out, n


def verdict(lost: int, k: int, base_rate: float | None) -> str:
    """lost_like: at least 3 of the nearest were lost and the share is at least twice the usual rate
    (and at least a third); renewed_like: none was lost; otherwise mixed. The page always shows the
    counts and the range next to this word."""
    if not k:
        return "mixed"
    share = lost / k
    if lost >= 3 and share >= max(2 * (base_rate or 0), 1 / 3):
        return "lost_like"
    if lost == 0:
        return "renewed_like"
    return "mixed"


def outcome_mix(neighbours: Sequence[Neighbour], base_rate: float | None) -> dict[str, Any]:
    k, lost = len(neighbours), sum(1 for n in neighbours if n.lost)
    ci = wilson(lost, k)
    return {
        "k": k,
        "lost": lost,
        "kept": k - lost,
        "lost_share": metric(lost / k if k else None, ci, k),
        "base_rate": None if base_rate is None else round(base_rate, 4),
        "verdict": verdict(lost, k, base_rate),
    }


# ---------------------------------------------------------------- the map (2-D projection)


def project(
    rows: Sequence[Features], model: StageModel, *, iterations: int = 200
) -> tuple[list[tuple[float, float]], list[dict[str, Any]]]:
    """Principal-component projection of patterns at one stage, in the model's own weighted units, so
    the map shows the same notion of "alike" the matching uses. Returns 2-D coordinates and, per axis,
    the numbers that move it most. Deterministic (fixed start vector)."""
    keys = [k for k, w in model.weights.items() if w > 0]
    if not keys or len(rows) < 3:
        return [(0.0, 0.0) for _ in rows], []
    values = {k: [s for r in rows if (s := scaled(k, r.get(k))) is not None] for k in keys}
    centre = {k: statistics.fmean(vs) if vs else 0.0 for k, vs in values.items()}

    def coord(r: Features, k: str) -> float:
        s = scaled(k, r.get(k))
        return 0.0 if s is None else math.sqrt(model.weights[k]) * (s - centre[k]) / model.spread[k]

    X = [[coord(r, k) for k in keys] for r in rows]
    p = len(keys)
    cov = [[sum(x[i] * x[j] for x in X) / len(X) for j in range(p)] for i in range(p)]
    axes: list[list[float]] = []
    for _axis in range(2):
        vec = [1.0 / math.sqrt(p) + 0.01 * i for i in range(p)]
        for _ in range(iterations):
            nxt = [sum(cov[i][j] * vec[j] for j in range(p)) for i in range(p)]
            for prev in axes:  # deflate: stay orthogonal to earlier axes
                dot = sum(a * b for a, b in zip(nxt, prev, strict=True))
                nxt = [a - dot * b for a, b in zip(nxt, prev, strict=True)]
            norm = math.sqrt(sum(a * a for a in nxt)) or 1.0
            vec = [a / norm for a in nxt]
        if max(vec, key=abs) < 0:  # a stable sign: the biggest loading is positive
            vec = [-a for a in vec]
        axes.append(vec)
    coords = [
        (
            round(sum(x[i] * axes[0][i] for i in range(p)), 4),
            round(sum(x[i] * axes[1][i] for i in range(p)), 4),
        )
        for x in X
    ]
    info: list[dict[str, Any]] = []
    for axis in axes:
        order = sorted(range(p), key=lambda i: -abs(axis[i]))[:3]
        info.append({"loadings": [{"feature": keys[i], "loading": round(axis[i], 3)} for i in order]})
    return coords, info


# ---------------------------------------------------------------- does matching beat the rules?


def library_at(cycles: Sequence[Cycle], day: date, stage: int) -> list[Cycle]:
    """The finished cycles a person could have learned from on ``day``: outcome already known, and
    observed at ``stage``. The only door to "the past" in the held-out test (no look-ahead)."""
    return [c for c in cycles if c.known_on < day and stage in c.points]


def evaluate(
    cycles: Sequence[Cycle], *, stages: Sequence[int] = (30, 60, 90), split: date | None = None
) -> dict[str, Any]:
    """Held-out test with no look-ahead. Each finished cycle in the later half is scored at a stage
    using ONLY cycles whose decision was already known at that moment (the library and the learned
    weights both), exactly as the live page would have scored it then. Compared with the rules risk
    score at the same moment; the difference has a paired bootstrap range."""
    if not cycles:
        return {"stages": {}, "split": None}
    split = split or statistics.median_low([c.end for c in cycles])
    out: dict[str, Any] = {"split": split.isoformat(), "stages": {}}
    for stage in stages:
        rows: list[tuple[float, float, bool]] = []
        for c in cycles:
            if c.end < split or stage not in c.points:
                continue
            at, _ = c.points[stage]
            known = library_at(cycles, at.date(), stage)
            model = learn_stage(stage, [(x.points[stage][1], x.lost) for x in known])
            if not model.ready:
                continue
            mine = {s: f for s, (_, f) in c.points.items()}
            near, _ = nearest(mine, known, model)
            if not near:
                continue
            match_score = sum(1 for n in near if n.lost) / len(near)
            rows.append((match_score, float(c.points[stage][1].get("risk") or 0.0), c.lost))
        y = [r[2] for r in rows]
        m_auc, r_auc = (
            auc([r[0] for r in rows], y) if rows else None,
            auc([r[1] for r in rows], y) if rows else None,
        )

        def diff(sample: Sequence[tuple[float, float, bool]]) -> float | None:
            a = auc([s[0] for s in sample], [s[2] for s in sample])
            b = auc([s[1] for s in sample], [s[2] for s in sample])
            return None if a is None or b is None else a - b

        d = diff(rows) if rows else None
        d_ci = bootstrap(rows, diff) if rows else None
        if d is None or d_ci is None:
            finding = "not enough history to test"
        elif sum(y) < MIN_TEST_LOST:
            finding = f"too few lost renewals to tell ({sum(y)})"
        elif d_ci[0] > 0:
            finding = "matching ranks lost renewals better than the rules"
        elif d_ci[1] < 0:
            finding = "the rules rank lost renewals better than matching"
        else:
            finding = "no clear difference between matching and the rules"
        out["stages"][str(stage)] = {
            "n": len(rows),
            "lost": sum(y),
            "matching_auc": metric(
                m_auc, bootstrap(rows, lambda s: auc([r[0] for r in s], [r[2] for r in s])), len(rows)
            ),
            "rules_auc": metric(
                r_auc, bootstrap(rows, lambda s: auc([r[1] for r in s], [r[2] for r in s])), len(rows)
            ),
            "difference": metric(d, d_ci, len(rows)),
            "finding": finding,
        }
    return out
