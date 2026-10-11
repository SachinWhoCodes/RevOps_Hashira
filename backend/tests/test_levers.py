"""Phase 6.2 — "What would lower this risk?" (pure code, no database, no AI)."""

from __future__ import annotations

from datetime import timedelta

from casefile.cases.levers import what_would_lower_risk
from casefile.scoring.points import default_scoring
from casefile.signals.base import REGISTRY, default_thresholds, run_signals
from casefile.timeline.snapshot import Snapshot
from tests.factories import (
    TODAY,
    at,
    contact,
    deal,
    email_in,
    email_out,
    meeting,
    snapshot,
    usage_days,
)

CFG = default_scoring()
DANA = "dana@acme.test"
RAVI = "ravi@acme.test"
AMARA = "amara@acme.test"


def acme_like() -> Snapshot:
    """Meeting gap, silent champion, empty next step and a 40% drop in active users: risk 70.
    Amara (a user) wrote in recently, so the relationship is not single-threaded."""
    return snapshot(
        contacts=[
            contact(DANA, name="Dana Whitfield"),
            contact(RAVI, "economic_buyer", name="Ravi Rao"),
            contact(AMARA, "user", name="Amara Menon"),
        ],
        activities=[
            meeting(at(30), [DANA]),
            email_in(at(40), AMARA, thread="z"),
            email_out(at(20), DANA, thread="a"),
            email_out(at(12), DANA, thread="b"),
        ],
        deals=[deal(close_in_days=43, next_step="")],
        usage=usage_days({(118, 28): (40, 400), (28, 0): (24, 400)}),
    )


def run(snap: Snapshot, **kw: object) -> dict:
    return what_would_lower_risk(snap, run_signals(snap), **kw)  # type: ignore[arg-type]


def test_each_lever_is_rescored_through_the_live_rules() -> None:
    out = run(acme_like())
    assert out["risk_now"] == 70
    got = {lv["key"]: (lv["clears"], lv["adds"], lv["risk_after"]) for lv in out["levers"]}
    assert got == {
        "buyer_meeting": (["meeting_gap"], ["executive_engagement"], 45),
        "usage_recovers": (["usage_decline"], [], 45),
        "champion_replies": (["champion_silence"], [], 50),
        "meeting_booked": (["meeting_gap"], [], 55),
        "next_step_set": (["missing_next_step"], [], 60),
    }
    # Biggest drop first.
    assert [lv["change"] for lv in out["levers"]] == sorted(lv["change"] for lv in out["levers"])


def test_single_signal_levers_match_the_points_table() -> None:
    for lv in run(acme_like())["levers"]:
        if len(lv["clears"]) == 1 and not lv["adds"]:
            assert lv["change"] == -CFG["risk_points"][lv["clears"][0]], lv["key"]


def test_buyer_meeting_catches_the_interaction_a_points_table_misses() -> None:
    lv = next(lv for lv in run(acme_like())["levers"] if lv["key"] == "buyer_meeting")
    # Ends the meeting gap (15) *and* earns the executive-engagement credit (10).
    both = CFG["risk_points"]["meeting_gap"] + CFG["positive_points"]["executive_engagement"]
    assert lv["change"] == -both
    assert lv["text"].startswith("Ravi Rao (economic buyer)")


def test_usage_target_is_the_smallest_number_the_rule_accepts() -> None:
    lv = next(lv for lv in run(acme_like())["levers"] if lv["key"] == "usage_recovers")
    assert "31 active users a day (now 24)" in lv["text"]
    assert "key actions" not in lv["text"]  # key actions did not fall; only users need to recover
    cfg = default_thresholds()["usage_decline"]
    fires = REGISTRY["usage_decline"].fn
    for users, expect in ((30, True), (31, False)):
        rows = usage_days({(118, 28): (40, 400), (28, 0): (users, 400)})
        assert bool(fires(snapshot(usage=rows), cfg)) is expect, users


def test_fewest_changes_to_close_the_case() -> None:
    close = run(acme_like())["to_close"]
    # Two changes are enough (risk below 30); of the pairs, this one leaves the lowest risk.
    assert close == {
        "needed": True,
        "possible": True,
        "levers": ["buyer_meeting", "usage_recovers"],
        "risk_after": 20,
    }


def test_departed_champion_cannot_be_cleared_and_says_when_it_ages_out() -> None:
    snap = snapshot(
        contacts=[contact("lee@acme.test", status="left", status_since=at(10))],
        activities=[meeting(at(5), ["lee@acme.test"])],
        deals=[deal(close_in_days=43, next_step="")],
    )
    out = run(snap)
    assert out["risk_now"] == 40
    assert [lv["key"] for lv in out["levers"]] == ["next_step_set"]
    (exp,) = out["expiries"]
    assert exp["signal"] == "champion_departure" and exp["risk_after"] == 10
    # Detected 10 days ago with a 120-day lookback: it stops counting 110 days from today.
    assert exp["stops_counting_on"] == (TODAY + timedelta(days=110)).isoformat()
    assert "not a sign of recovery" in exp["text"]
    # No economic buyer on record, so nothing we can do brings it below 30 before then.
    close = out["to_close"]
    assert (close["possible"], close["levers"], close["risk_after"]) == (False, ["next_step_set"], 30)


def test_plan_links_and_direct_next_step_action() -> None:
    out = run(acme_like(), plan_play="champion_silent_renewal_near")
    by = {lv["key"]: lv for lv in out["levers"]}
    assert by["champion_replies"]["in_plan"] and not by["meeting_booked"]["in_plan"]
    assert by["next_step_set"]["in_plan"] and by["next_step_set"]["direct_action"] == "crm_next_step"
    assert run(acme_like())["levers"][0]["in_plan"] is False  # no plan, no claim


def test_no_renewal_deal_lever() -> None:
    snap = snapshot(
        activities=[meeting(at(5), [DANA, AMARA])],
        contacts=[contact(DANA), contact(AMARA, "user")],
        renewal_in_days=30,
    )
    (lv,) = run(snap)["levers"]
    assert lv["key"] == "next_step_set" and lv["clears"] == ["missing_next_step"]
    assert lv["text"].startswith("A renewal deal is opened") and lv["direct_action"] is None


def test_healthy_account_has_nothing_to_lower() -> None:
    snap = snapshot(
        contacts=[contact(DANA), contact(AMARA, "user")],
        activities=[meeting(at(5), [DANA, AMARA])],
        deals=[deal(next_step="Renewal call", since_days_ago=3)],
    )
    out = run(snap)
    assert out["risk_now"] == 0 and out["levers"] == [] and out["expiries"] == []
    assert out["to_close"]["needed"] is False


def test_analysis_never_changes_the_snapshot() -> None:
    snap = acme_like()
    before = (snap.activities, snap.deals, snap.usage, snap.account)
    run(snap)
    assert (snap.activities, snap.deals, snap.usage, snap.account) == before


def test_a_line_the_plan_already_wrote_is_marked_not_read_back() -> None:
    out = run(acme_like(), plan_play="champion_silent_renewal_near", plan_done=frozenset({"crm_next_step"}))
    by = {lv["key"]: lv for lv in out["levers"]}
    # Written to the CRM, but the score is built from what was last read: say so, don't hide it.
    assert by["next_step_set"]["done_by_plan"] and not by["champion_replies"]["done_by_plan"]
    assert not run(acme_like())["levers"][0]["done_by_plan"]


def test_no_reply_lever_for_a_champion_who_has_left() -> None:
    # The CRM still says "active", but mail bounces: silence and departure both fire for one person.
    lee = "lee@acme.test"
    snap = snapshot(
        contacts=[contact(lee, name="Lee Chen")],
        activities=[
            meeting(at(5), [lee]),
            email_out(at(20), lee, thread="a"),
            email_out(at(12), lee, thread="b"),
            email_in(at(12), "mailer-daemon@acme.test", thread="b", bounce_for=lee),
        ],
        deals=[deal(next_step="Renewal call", since_days_ago=3)],
    )
    hits = run_signals(snap)
    # Rules v4 score the departure once: silence from someone who has left is not a second signal.
    assert "champion_departure" in {h.type for h in hits} and "champion_silence" not in {h.type for h in hits}
    out = what_would_lower_risk(snap, hits)
    assert "champion_replies" not in {lv["key"] for lv in out["levers"]}
    # Even under rules that still score the silence (v3), the lever never asks a departed person to reply.
    v3 = {**default_thresholds(), "champion_silence": {**default_thresholds()["champion_silence"]}}
    v3["champion_silence"].pop("skip_if_departed_within_days")
    old_hits = run_signals(snap, v3)
    assert {"champion_silence", "champion_departure"} <= {h.type for h in old_hits}
    old = what_would_lower_risk(snap, old_hits, thresholds=v3)
    assert "champion_replies" not in {lv["key"] for lv in old["levers"]}


def test_single_threaded_lever_names_who_could_break_the_single_thread() -> None:
    snap = snapshot(
        contacts=[contact(DANA, name="Dana Whitfield"), contact(RAVI, "economic_buyer", name="Ravi Rao")],
        activities=[meeting(at(10), [DANA])],
        deals=[deal(next_step="Renewal call", since_days_ago=3)],
    )
    by = {lv["key"]: lv for lv in run(snap)["levers"]}
    assert by["second_person"]["clears"] == ["single_threaded"] and by["second_person"]["risk_after"] == 0
    assert "Ravi Rao" in by["second_person"]["text"]  # the economic buyer first
    # A meeting with the buyer also breaks the single thread, and earns the engagement credit.
    assert by["buyer_meeting"]["clears"] == ["single_threaded"]
    assert by["buyer_meeting"]["adds"] == ["executive_engagement"]


def test_single_threaded_with_nobody_else_on_record_suggests_finding_someone() -> None:
    snap = snapshot(
        contacts=[contact(DANA, name="Dana Whitfield")],
        activities=[meeting(at(10), [DANA])],
        deals=[deal(next_step="Renewal call", since_days_ago=3)],
    )
    (lv,) = run(snap)["levers"]
    assert lv["text"] == "A second contact at the customer is found and engages"
    assert lv["clears"] == ["single_threaded"]
