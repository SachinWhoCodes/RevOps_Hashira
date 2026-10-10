"""One test group per signal, including the blueprint's edge cases."""

from __future__ import annotations

from datetime import datetime, timedelta

from casefile.signals.base import default_thresholds
from casefile.signals.crm import missing_next_step, renewal_proximity
from casefile.signals.engagement import (
    champion_departure,
    champion_silence,
    executive_engagement,
    meeting_gap,
)
from casefile.signals.usage import new_team_adoption, seat_saturation, usage_decline
from tests.factories import (
    TODAY,
    TZ,
    at,
    contact,
    deal,
    email_in,
    email_out,
    meeting,
    snapshot,
    usage_days,
)

T = default_thresholds()
DANA = "dana@acme.test"


# ---------------------------------------------------------------- meeting_gap


def test_meeting_gap_fires_at_exactly_21_days() -> None:
    hits = meeting_gap(snapshot(activities=[meeting(at(21), [DANA])]), T["meeting_gap"])
    assert len(hits) == 1 and hits[0].facts["days_since_last_meeting"] == 21


def test_meeting_gap_silent_at_20_days() -> None:
    assert meeting_gap(snapshot(activities=[meeting(at(20), [DANA])]), T["meeting_gap"]) == []


def test_meeting_gap_silent_when_a_meeting_is_booked() -> None:
    acts = [meeting(at(40), [DANA]), meeting(at(-5), [DANA])]
    assert meeting_gap(snapshot(activities=acts), T["meeting_gap"]) == []


def test_cancelled_meetings_are_neither_held_nor_booked() -> None:
    acts = [
        meeting(at(30), [DANA]),
        meeting(at(10), [DANA], cancelled=True),
        meeting(at(-3), [DANA], cancelled=True),
    ]
    hits = meeting_gap(snapshot(activities=acts), T["meeting_gap"])
    assert hits and hits[0].facts["days_since_last_meeting"] == 30
    assert hits[0].facts["meetings_cancelled_recently"] == 2


def test_meeting_gap_uses_local_calendar_days() -> None:
    # 00:30 IST on 20 Sep is still 19 Sep in UTC. Counted in local days it is 20 days ago: no signal.
    m = meeting(datetime(2026, 9, 20, 0, 30, tzinfo=TZ), [DANA])
    assert meeting_gap(snapshot(activities=[m]), T["meeting_gap"]) == []


def test_meeting_gap_never_met_is_a_blind_spot_not_a_signal() -> None:
    assert meeting_gap(snapshot(activities=[]), T["meeting_gap"]) == []


# ---------------------------------------------------------------- champion_silence


def _champ() -> list:
    return [contact(DANA, "champion", name="Dana")]


def test_champion_silence_fires() -> None:
    acts = [email_in(at(30), DANA), email_out(at(16), DANA, thread="t2"), email_out(at(9), DANA, thread="t2")]
    hits = champion_silence(snapshot(activities=acts, contacts=_champ()), T["champion_silence"])
    assert len(hits) == 1
    f = hits[0].facts
    assert (
        f["unanswered_emails"] == 2
        and f["days_since_first_unanswered"] == 16
        and f["days_since_last_reply"] == 30
    )


def test_champion_silence_needs_ten_days() -> None:
    acts = [email_out(at(9), DANA), email_out(at(3), DANA)]
    assert champion_silence(snapshot(activities=acts, contacts=_champ()), T["champion_silence"]) == []


def test_reply_from_personal_address_in_the_thread_counts() -> None:
    acts = [
        email_out(at(16), DANA, thread="t2"),
        email_out(at(12), DANA, thread="t2"),
        email_in(at(11), "dana.home@gmail.com", thread="t2"),
    ]
    assert champion_silence(snapshot(activities=acts, contacts=_champ()), T["champion_silence"]) == []


def test_auto_reply_is_not_a_reply() -> None:
    acts = [email_out(at(16), DANA), email_in(at(16), DANA, auto_reply=True), email_out(at(9), DANA)]
    assert champion_silence(snapshot(activities=acts, contacts=_champ()), T["champion_silence"])


def test_reply_from_a_colleague_is_not_the_champion_replying() -> None:
    contacts = [*_champ(), contact("raj@acme.test", "user")]
    acts = [email_out(at(16), DANA), email_in(at(15), "raj@acme.test"), email_out(at(9), DANA)]
    assert champion_silence(snapshot(activities=acts, contacts=contacts), T["champion_silence"])


# ---------------------------------------------------------------- usage_decline


def test_sudden_usage_drop_fires_and_quotes_its_own_numbers() -> None:
    u = usage_days({(56, 28): (40, 400), (28, 0): (28, 380)})
    hits = usage_decline(snapshot(usage=u), T["usage_decline"])
    assert len(hits) == 1
    f = hits[0].facts
    assert f["decline_kind"] == "sudden" and f["active_users_change_pct"] == -30
    assert "40 → 28" in hits[0].summary


def test_small_drop_does_not_fire() -> None:
    u = usage_days({(118, 28): (40, 400), (28, 0): (34, 380)})
    assert usage_decline(snapshot(usage=u), T["usage_decline"]) == []


def test_slow_slide_caught_against_the_quarter() -> None:
    u = usage_days({(118, 56): (50, 500), (56, 28): (40, 400), (28, 0): (33, 330)})
    hits = usage_decline(snapshot(usage=u), T["usage_decline"])
    assert hits and hits[0].facts["decline_kind"] == "gradual"
    assert hits[0].facts["active_users_change_vs_baseline_pct"] <= -30


def test_tiny_baseline_is_ignored() -> None:
    u = usage_days({(56, 28): (2, 10), (28, 0): (1, 5)})
    assert usage_decline(snapshot(usage=u), T["usage_decline"]) == []


def test_not_enough_days_of_data() -> None:
    u = usage_days({(56, 46): (40, 400), (28, 0): (10, 100)})
    assert usage_decline(snapshot(usage=u), T["usage_decline"]) == []


def test_usage_ignores_today_and_future_rows() -> None:
    u = usage_days({(56, 28): (40, 400), (28, 0): (40, 400)})
    assert usage_decline(snapshot(usage=u), T["usage_decline"]) == []


# ---------------------------------------------------------------- renewal_proximity


def test_renewal_proximity_edges() -> None:
    assert renewal_proximity(snapshot(renewal_in_days=90), T["renewal_proximity"])
    assert renewal_proximity(snapshot(renewal_in_days=91), T["renewal_proximity"]) == []
    assert renewal_proximity(snapshot(renewal_in_days=None), T["renewal_proximity"]) == []


def test_overdue_renewal_only_with_an_open_deal() -> None:
    assert renewal_proximity(snapshot(renewal_in_days=-5), T["renewal_proximity"]) == []
    hits = renewal_proximity(
        snapshot(renewal_in_days=-5, deals=[deal(close_in_days=-5)]), T["renewal_proximity"]
    )
    assert hits and hits[0].facts["overdue"] is True


# ---------------------------------------------------------------- missing_next_step


def test_cleared_next_step() -> None:
    hits = missing_next_step(
        snapshot(deals=[deal(next_step="", since_days_ago=25, ever_set=True)]), T["missing_next_step"]
    )
    assert hits and "cleared on" in hits[0].summary and hits[0].facts["next_step_cleared"] is True


def test_never_set_next_step_is_not_called_cleared() -> None:
    hits = missing_next_step(snapshot(deals=[deal(next_step="", ever_set=False)]), T["missing_next_step"])
    assert hits and "never had" in hits[0].summary


def test_stale_next_step() -> None:
    hits = missing_next_step(
        snapshot(deals=[deal(next_step="Call Dana", since_days_ago=35)]), T["missing_next_step"]
    )
    assert hits and hits[0].facts["reason"] == "stale"


def test_fresh_next_step_is_fine() -> None:
    assert (
        missing_next_step(
            snapshot(deals=[deal(next_step="Call Dana", since_days_ago=5)]), T["missing_next_step"]
        )
        == []
    )


def test_next_step_date_passed() -> None:
    d = deal(next_step="Send quote", since_days_ago=5, next_step_date=TODAY - timedelta(days=2))
    hits = missing_next_step(snapshot(deals=[d]), T["missing_next_step"])
    assert hits and hits[0].facts["reason"] == "date_passed"


def test_no_renewal_deal_close_to_renewal() -> None:
    hits = missing_next_step(snapshot(deals=[], renewal_in_days=30), T["missing_next_step"])
    assert hits and hits[0].facts["reason"] == "no_renewal_deal"


def test_far_renewal_not_checked() -> None:
    assert (
        missing_next_step(
            snapshot(deals=[deal(close_in_days=150)], renewal_in_days=150), T["missing_next_step"]
        )
        == []
    )


# ---------------------------------------------------------------- champion_departure


def test_bounce_means_departure() -> None:
    acts = [email_out(at(5), DANA), email_in(at(5), "mailer-daemon@googlemail.com", bounce_for=DANA)]
    hits = champion_departure(snapshot(activities=acts, contacts=_champ()), T["champion_departure"])
    assert hits and hits[0].facts["how"] == ["email bounced"]


def test_left_company_auto_reply() -> None:
    acts = [
        email_in(at(5), DANA, auto_reply=True, snippet="I have left Acme and this mailbox is not monitored")
    ]
    assert champion_departure(snapshot(activities=acts, contacts=_champ()), T["champion_departure"])


def test_out_of_office_is_not_departure() -> None:
    acts = [email_in(at(5), DANA, auto_reply=True, snippet="I am out of the office until Monday")]
    assert champion_departure(snapshot(activities=acts, contacts=_champ()), T["champion_departure"]) == []


def test_crm_status_left() -> None:
    contacts = [contact(DANA, "champion", status="left", status_since=at(10))]
    hits = champion_departure(snapshot(contacts=contacts), T["champion_departure"])
    assert hits and hits[0].facts["how"] == ["CRM marked them as left"]


# ---------------------------------------------------------------- opportunity and positive


def test_seat_saturation() -> None:
    assert seat_saturation(
        snapshot(usage=usage_days({(14, 0): (60, 600)}, seats_used=92), seats=100), T["seat_saturation"]
    )
    assert (
        seat_saturation(
            snapshot(usage=usage_days({(14, 0): (60, 600)}, seats_used=85), seats=100), T["seat_saturation"]
        )
        == []
    )
    assert (
        seat_saturation(
            snapshot(usage=usage_days({(14, 0): (60, 600)}, seats_used=92), seats=None), T["seat_saturation"]
        )
        == []
    )


def test_new_team_adoption() -> None:
    u = usage_days(
        {(118, 0): (20, 200)}, departments={(118, 20): {"Sales": 20}, (20, 0): {"Sales": 15, "Finance": 5}}
    )
    hits = new_team_adoption(snapshot(usage=u), T["new_team_adoption"])
    assert [h.facts["department"] for h in hits] == ["Finance"]


def test_existing_team_is_not_new() -> None:
    u = usage_days({(118, 0): (20, 200)}, departments={(118, 0): {"Sales": 15, "Finance": 5}})
    assert new_team_adoption(snapshot(usage=u), T["new_team_adoption"]) == []


def test_new_team_needs_a_baseline() -> None:
    u = usage_days({(20, 0): (20, 200)}, departments={(20, 0): {"Finance": 5}})
    assert new_team_adoption(snapshot(usage=u), T["new_team_adoption"]) == []


def test_executive_engagement() -> None:
    buyer = contact("cfo@acme.test", "economic_buyer", name="Cara")
    assert executive_engagement(
        snapshot(activities=[meeting(at(30), ["cfo@acme.test"])], contacts=[buyer]), T["executive_engagement"]
    )
    assert (
        executive_engagement(
            snapshot(activities=[meeting(at(70), ["cfo@acme.test"])], contacts=[buyer]),
            T["executive_engagement"],
        )
        == []
    )
    cancelled = meeting(at(30), ["cfo@acme.test"], cancelled=True)
    assert (
        executive_engagement(snapshot(activities=[cancelled], contacts=[buyer]), T["executive_engagement"])
        == []
    )


def test_every_fact_is_json_ready_and_every_hit_has_evidence() -> None:
    import json

    from casefile.signals.base import run_signals

    acts = [meeting(at(30), [DANA]), email_out(at(16), DANA), email_out(at(9), DANA)]
    snap = snapshot(
        activities=acts,
        contacts=_champ(),
        deals=[deal(next_step="", ever_set=True)],
        usage=usage_days({(56, 28): (40, 400), (28, 0): (20, 200)}, seats_used=95),
    )
    hits = run_signals(snap)
    assert {h.type for h in hits} >= {
        "meeting_gap",
        "champion_silence",
        "missing_next_step",
        "usage_decline",
        "renewal_proximity",
        "seat_saturation",
    }
    for h in hits:
        json.dumps(h.to_dict())
        assert h.evidence, h.type
        assert h.summary.endswith(".")
