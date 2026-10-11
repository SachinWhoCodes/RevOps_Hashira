"""Renewal patterns: features, learning, matching, the no-look-ahead test, training and the API."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.api.deps import get_db
from casefile.api.main import app
from casefile.bootstrap import upsert_user
from casefile.db.models import CyclePattern, Org, PatternMatch, PatternModel, UserRole
from casefile.patterns.features import MAX_DAYS, describe
from casefile.patterns.matching import (
    Cycle,
    StageModel,
    beyond_chance,
    evaluate,
    learn_stage,
    library_at,
    nearest,
    path_distance,
    project,
    verdict,
)
from casefile.patterns.train import stage_for, train
from casefile.timeline.snapshot import AccountHistory
from tests import factories as f

CSRF = {"x-casefile-csrf": "1"}


def _org(s: Session) -> Org:
    return s.scalars(select(Org).where(Org.slug == "test-org")).one()


# ---------------------------------------------------------------- features (pure)


def test_usage_is_compared_with_the_clients_own_past() -> None:
    usage = f.usage_days({(220, 180): (40, 400), (180, 28): (40, 400), (28, 0): (20, 200)}, seats_used=50)
    snap = f.snapshot(usage=usage, seats=100)
    out = describe(snap, [], 35)
    assert out["risk"] == 35.0
    assert out["usage.users_vs_6mo"] == -0.5  # 20 a day now vs 40 six months earlier
    assert out["usage.users_vs_prior"] == -0.5
    assert out["usage.seat_use"] == 0.5
    # same shape of decline at ten times the size gives the same pattern
    big = describe(
        f.snapshot(usage=f.usage_days({(220, 28): (400, 4000), (28, 0): (200, 2000)}), seats=None), [], 35
    )
    assert big["usage.users_vs_6mo"] == out["usage.users_vs_6mo"] and big["usage.seat_use"] is None


def test_no_usage_means_no_value_not_zero() -> None:
    out = describe(f.snapshot(), [], 0)
    assert out["usage.users_vs_6mo"] is None and out["usage.seat_use"] is None
    assert out["eng.days_since_meeting"] == MAX_DAYS and out["eng.meetings_90d"] == 0.0


def test_meetings_and_customer_email_counts() -> None:
    champ = "kim@acme.test"
    acts = [
        f.meeting(f.at(10), [champ]),
        f.meeting(f.at(40), [champ]),
        f.meeting(f.at(5), [champ], cancelled=True),
        f.email_in(f.at(3), champ),
        f.email_in(f.at(90), champ),  # outside 60 days
    ]
    out = describe(f.snapshot(activities=acts), [], 0)
    assert out["eng.days_since_meeting"] == 10.0  # the cancelled one does not count
    assert out["eng.meetings_90d"] == 2.0
    assert out["eng.customer_emails_60d"] == 1.0


# ---------------------------------------------------------------- learning and matching (pure)


def _rows(n_kept: int, n_lost: int, *, informative: bool) -> list[tuple[dict[str, float | None], bool]]:
    rows: list[tuple[dict[str, float | None], bool]] = []
    for i in range(n_kept):
        rows.append(({"risk": 10.0 + i % 5 if informative else float(i % 7), "eng.meetings_90d": 3.0}, False))
    for i in range(n_lost):
        rows.append(({"risk": 60.0 + i % 5 if informative else float(i % 7), "eng.meetings_90d": 3.0}, True))
    return rows


def test_a_number_counts_only_when_it_separated_outcomes_beyond_chance() -> None:
    sharp = learn_stage(30, _rows(40, 10, informative=True))
    assert sharp.weights["risk"] > 0.8 and sharp.ready
    assert sharp.weights["eng.meetings_90d"] == 0.0  # identical for everyone: tells nothing
    noise = learn_stage(30, _rows(40, 10, informative=False))
    assert noise.weights["risk"] == 0.0 and not noise.ready  # no verdicts from noise
    # an AUC of 0.6 is luck with 10 lost cycles, real with 1,000
    assert beyond_chance(0.6, 10, 40) == 0.0
    assert beyond_chance(0.6, 1000, 4000) > 0.1


def test_too_few_lost_cycles_is_not_ready() -> None:
    assert not learn_stage(30, _rows(40, 2, informative=True)).ready
    assert not learn_stage(30, _rows(15, 4, informative=True)).ready


def _cycle(
    key: str, end: date, lost: bool, risk_by_stage: dict[int, float], known: date | None = None
) -> Cycle:
    at = datetime.combine(end, datetime.min.time(), UTC)
    return Cycle(
        key=key,
        end=end,
        lost=lost,
        points={s: (at - timedelta(days=s), {"risk": r}) for s, r in risk_by_stage.items()},
        known=known,
    )


def test_path_distance_compares_the_path_and_needs_the_stage() -> None:
    model = StageModel(30, 50, 10, 0.2, weights={"risk": 1.0}, spread={"risk": 10.0})
    same = path_distance(
        {30: {"risk": 50.0}, 60: {"risk": 40.0}}, {30: {"risk": 50.0}, 60: {"risk": 40.0}}, model
    )
    assert same == 0.0
    near_now = path_distance(
        {30: {"risk": 50.0}, 60: {"risk": 40.0}}, {30: {"risk": 50.0}, 60: {"risk": 0.0}}, model
    )
    far_now = path_distance(
        {30: {"risk": 50.0}, 60: {"risk": 40.0}}, {30: {"risk": 10.0}, 60: {"risk": 40.0}}, model
    )
    assert near_now is not None and far_now is not None and near_now < far_now  # nearer moments count more
    assert path_distance({60: {"risk": 1.0}}, {30: {"risk": 1.0}}, model) is None


def test_nearest_is_sorted_reproducible_and_counts_compared() -> None:
    model = StageModel(30, 50, 10, 0.2, weights={"risk": 1.0}, spread={"risk": 10.0})
    library = [_cycle(f"c{i}", date(2026, 1, 1), i % 3 == 0, {30: float(i * 10)}) for i in range(12)]
    near, compared = nearest({30: {"risk": 42.0}}, library, model, k=3)
    assert compared == 12 and [n.key for n in near] == ["c4", "c5", "c3"]
    assert near[0].closer_than == 1.0 and 0 <= near[-1].closer_than < 1
    assert nearest({30: {"risk": 42.0}}, list(reversed(library)), model, k=3)[0] == near


def test_verdict_words() -> None:
    assert verdict(5, 9, 0.19) == "lost_like"
    assert verdict(2, 9, 0.19) == "mixed"  # fewer than three lost: never a strong word
    assert verdict(3, 9, 0.25) == "mixed"  # 33% is not twice the usual 25%
    assert verdict(0, 9, 0.19) == "renewed_like"


def test_the_held_out_test_never_uses_an_outcome_from_the_future() -> None:
    early = _cycle("early", date(2026, 1, 1), True, {30: 50.0})
    decided_late = _cycle("late", date(2026, 1, 20), False, {30: 50.0}, known=date(2026, 3, 1))
    other_stage = _cycle("other", date(2026, 1, 1), False, {60: 50.0})
    lib = library_at([early, decided_late, other_stage], date(2026, 2, 1), 30)
    assert [c.key for c in lib] == ["early"]  # decided 1 Mar: unknown on 1 Feb even though it ended in Jan


def test_evaluate_reports_n_ranges_and_a_plain_finding() -> None:
    cycles = [
        _cycle(
            f"c{i}",
            date(2025, 1, 1) + timedelta(days=7 * i),
            i % 4 == 0,
            {30: 70.0 if i % 4 == 0 else 10.0 + i % 3},
        )
        for i in range(80)
    ]
    out = evaluate(cycles, stages=(30,))
    block = out["stages"]["30"]
    assert block["n"] > 0 and block["matching_auc"]["n"] == block["n"]
    assert block["finding"] in {
        "matching ranks lost renewals better than the rules",
        "the rules rank lost renewals better than matching",
        "no clear difference between matching and the rules",
    }


def test_projection_is_deterministic_and_names_its_axes() -> None:
    model = learn_stage(30, _rows(30, 10, informative=True))
    rows = [r for r, _ in _rows(30, 10, informative=True)]
    a, axes = project(rows, model)
    assert project(rows, model)[0] == a and len(a) == len(rows)
    assert axes and axes[0]["loadings"][0]["feature"] == "risk"


def test_stage_for_rounds_to_a_checkpoint_within_range() -> None:
    assert [stage_for(d) for d in (-10, 0, 44, 46, 200, 900)] == [30, 30, 30, 60, 210, 360]


# ---------------------------------------------------------------- training on the synced company


def test_training_stores_patterns_matches_and_uses_only_the_past(synced: Session) -> None:
    org = _org(synced)
    model = train(synced, org)
    assert model.status in {"learning", "ready"} and model.label.value == "simulated"
    assert model.stats["described_cycles"] > 0 and model.stats["clients"] > 0
    assert set(model.stats["outcomes"]) <= {"renewed", "late", "downgraded", "churned"}

    cycle = synced.scalars(select(CyclePattern).where(CyclePattern.model_id == model.id)).first()
    assert cycle is not None
    for stage, point in cycle.points.items():
        at = datetime.fromisoformat(point["at"])
        assert at.date() <= cycle.cycle_end - timedelta(days=int(stage)) + timedelta(days=1)
        # rule R3: the stored pattern is exactly what the as-of snapshot says at that moment
        snap = AccountHistory.load(synced, cycle.account_id).as_of(at)
        assert snap is not None
        assert point["f"]["eng.meetings_90d"] == describe(snap, [], 0)["eng.meetings_90d"]

    matches = list(synced.scalars(select(PatternMatch).where(PatternMatch.model_id == model.id)))
    assert matches
    for m in matches:
        if m.verdict is not None:
            assert m.k and len(m.detail["neighbours"]) == m.k and m.detail["mix"]["lost_share"]["n"] == m.k
        for point in m.detail.get("trajectory", []):
            assert datetime.fromisoformat(point["at"]) <= m.computed_at

    again = train(synced, org)
    assert again.version == model.version + 1


def test_a_company_with_no_history_still_gets_a_library(db: Session) -> None:
    from casefile.bootstrap import ensure_org

    org = ensure_org(
        db, slug="empty-co", name="Empty", timezone="Asia/Kolkata", internal_domains=["empty.test"]
    )
    model = train(db, org)
    assert model.status == "no_history" and model.stats["described_cycles"] == 0
    assert model.evaluation == {"stages": {}, "split": None}


def test_old_trainings_are_pruned(synced: Session) -> None:
    org = _org(synced)
    for _ in range(4):
        train(synced, org)
    versions = list(synced.scalars(select(PatternModel.version).where(PatternModel.org_id == org.id)))
    assert len(versions) == 3


# ---------------------------------------------------------------- API


@pytest.fixture(scope="module")
def client(synced: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: synced
    with TestClient(app) as c:
        r = c.post(
            "/api/auth/login",
            json={"email": "admin@lumora.test", "password": "test-password-1"},
            headers=CSRF,
        )
        assert r.status_code == 200
        yield c
    app.dependency_overrides.clear()


def test_patterns_api(client: TestClient, synced: Session) -> None:
    trained = client.post("/api/patterns/train", headers=CSRF).json()
    assert trained["model"]["status"] in {"learning", "ready"} and trained["can_train"] is True
    assert len(trained["stages"]) == 12 and {"stage", "n", "lost", "ready", "top"} <= set(
        trained["stages"][0]
    )
    over = client.get("/api/patterns").json()
    assert over["model"]["version"] == trained["model"]["version"]

    rows = client.get("/api/patterns/clients").json()
    assert rows and {"account_id", "stage", "verdict", "neighbours", "days_to_renewal"} <= set(rows[0])
    placed = next(r for r in rows if r["stage"] is not None)
    one = client.get(f"/api/patterns/clients/{placed['account_id']}").json()
    assert one["name"] == placed["name"] and one["label"] == "simulated"
    assert one["now"]["days"] == placed["days_to_renewal"]
    for n in one["neighbours"]:
        assert n["cycle"]["points"] and n["cycle"]["outcome"]

    stage = next((s["stage"] for s in over["stages"] if s["ready"]), 30)
    m = client.get(f"/api/patterns/map?stage={stage}").json()
    assert m["stage"] == stage and isinstance(m["points"], list)
    if m["ready"]:
        assert all({"x", "y", "kind"} <= set(p) for p in m["points"])

    s = client.get(f"/api/patterns/strand/{placed['account_id']}").json()
    assert s["weeks"] and {"emails_in", "emails_out", "meetings", "users"} <= set(s["weeks"][0])
    assert "subject" not in str(s)  # counts only
    assert client.get("/api/patterns/map?stage=45").status_code == 404


def test_only_admins_train(client: TestClient, synced: Session) -> None:
    org = _org(synced)
    upsert_user(
        synced,
        org,
        email="owner.p@lumora.test",
        name="Owner P",
        role=UserRole.owner,
        password="owner-pass-12",
    )
    synced.commit()
    with TestClient(app) as other:
        assert (
            other.post(
                "/api/auth/login",
                json={"email": "owner.p@lumora.test", "password": "owner-pass-12"},
                headers=CSRF,
            ).status_code
            == 200
        )
        assert other.post("/api/patterns/train", headers=CSRF).status_code == 403
        assert other.get("/api/patterns").json()["can_train"] is False
