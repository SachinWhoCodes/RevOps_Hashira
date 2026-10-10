"""Golden tests for the points model."""

from __future__ import annotations

from casefile.db.models import Polarity
from casefile.scoring.points import confidence, default_scoring, score, signature, urgency_factor
from casefile.signals.base import Evidence, SignalHit
from tests.factories import snapshot

CFG = default_scoring()


def hit(kind: str, polarity: Polarity = Polarity.risk, evidence: str = "e1") -> SignalHit:
    return SignalHit(kind, polarity, 1.0, f"{kind}.", evidence=(Evidence("activity", evidence, kind),))


ACME = [
    hit("missing_next_step"),
    hit("meeting_gap"),
    hit("champion_silence"),
    hit("usage_decline"),
    hit("renewal_proximity", Polarity.urgency),
]


def test_acme_like_case_scores_70_with_breakdown() -> None:
    s = score(snapshot(renewal_in_days=43, arr="2137000"), ACME, CFG)
    assert s.risk == 10 + 15 + 20 + 25
    assert [line["signal"] for line in s.breakdown["risk"]["lines"]] == [
        "missing_next_step",
        "meeting_gap",
        "champion_silence",
        "usage_decline",
    ]
    assert s.breakdown["urgency"]["factor"] == 0.8
    assert s.priority == round(0.70 * 2137000 * 0.8, 2)


def test_positive_signal_subtracts_and_risk_never_negative() -> None:
    s = score(snapshot(), [hit("meeting_gap"), hit("executive_engagement", Polarity.positive)], CFG)
    assert s.risk == 5
    s = score(snapshot(), [hit("executive_engagement", Polarity.positive)], CFG)
    assert s.risk == 0


def test_risk_is_capped_at_100() -> None:
    hits = [
        hit(k)
        for k in (
            "champion_departure",
            "usage_decline",
            "champion_silence",
            "meeting_gap",
            "missing_next_step",
        )
    ]
    assert score(snapshot(), hits * 2, CFG).risk == 100


def test_repeated_signal_type_counts_once() -> None:
    assert (
        score(
            snapshot(), [hit("champion_silence", evidence="a"), hit("champion_silence", evidence="b")], CFG
        ).risk
        == 20
    )


def test_opportunity_is_separate_from_risk() -> None:
    s = score(
        snapshot(),
        [hit("seat_saturation", Polarity.opportunity), hit("new_team_adoption", Polarity.opportunity)],
        CFG,
    )
    assert (s.risk, s.opportunity) == (0, 60)


def test_unknown_arr_uses_fallback_and_says_so() -> None:
    s = score(snapshot(arr=None), [hit("meeting_gap")], CFG, arr_fallback=1_000_000)  # type: ignore[arg-type]
    assert s.breakdown["arr"] == {"value": 1_000_000.0, "assumed": True}


def test_urgency_table() -> None:
    assert urgency_factor(30, CFG) == 1.0
    assert urgency_factor(31, CFG) == 0.8
    assert urgency_factor(90, CFG) == 0.6
    assert urgency_factor(400, CFG) == 0.2
    assert urgency_factor(None, CFG) == CFG["urgency_unknown"]
    assert urgency_factor(-3, CFG) == CFG["urgency_overdue"]


def test_confidence_from_sources() -> None:
    assert confidence(snapshot(sources={"crm", "gmail", "gcal", "usage"}), CFG)[0] == "high"
    assert confidence(snapshot(sources={"crm", "gmail", "gcal"}), CFG)[0] == "medium"
    level, _, missing = confidence(snapshot(sources={"crm", "gmail"}), CFG)
    assert level == "low" and missing == ["calendar", "usage"]


def test_signature_ignores_day_counts_but_not_evidence() -> None:
    a = SignalHit("meeting_gap", Polarity.risk, 21, "21 days.", evidence=(Evidence("activity", "m1", "x"),))
    b = SignalHit("meeting_gap", Polarity.risk, 22, "22 days.", evidence=(Evidence("activity", "m1", "x"),))
    c = SignalHit("meeting_gap", Polarity.risk, 22, "22 days.", evidence=(Evidence("activity", "m2", "x"),))
    assert signature([a]) == signature([b]) != signature([c])
