"""Phase 6.3 relationship map: touch counts, last heard from, who is active."""

from __future__ import annotations

from datetime import timedelta

from casefile.cases.relationships import relationship_map
from tests.factories import TODAY, at, contact, email_in, email_out, meeting, snapshot

DANA, RAVI, AMARA, LEE = "dana@acme.test", "ravi@acme.test", "amara@acme.test", "lee@acme.test"


def people() -> list:
    return [
        contact(DANA, "champion", name="Dana"),
        contact(RAVI, "economic_buyer", name="Ravi"),
        contact(AMARA, "user", name="Amara"),
        contact(LEE, "user", name="Lee", status="left", status_since=at(100)),
    ]


def test_map_counts_touches_and_who_we_last_heard_from() -> None:
    snap = snapshot(
        contacts=people(),
        activities=[
            email_in(at(12), DANA),
            email_in(at(40), DANA, thread="t2"),
            email_out(at(5), DANA, thread="t3"),
            email_in(at(3), AMARA, auto_reply=True, snippet="Out of office"),  # not a reply
            email_out(at(3), AMARA),
            meeting(at(20), [DANA, RAVI]),
            meeting(at(8), [RAVI], cancelled=True),  # did not happen
            meeting(at(-6), [RAVI]),  # booked for next week
            email_in(at(120), LEE),  # outside the 90-day window, and Lee has left
        ],
    )
    m = relationship_map(snap)
    by = {p["email"]: p for p in m["people"]}
    dana, ravi, amara, lee = by[DANA], by[RAVI], by[AMARA], by[LEE]
    assert (dana["emails_from"], dana["emails_to"], dana["meetings"]) == (2, 1, 1)
    assert dana["days_since_heard"] == 12 and dana["active"]
    assert ravi["days_since_heard"] == 20 and ravi["meetings"] == 1 and ravi["active"]
    assert ravi["next_meeting"] == (TODAY + timedelta(days=6)).isoformat()
    assert amara["last_heard_from"] is None and amara["emails_to"] == 1 and not amara["active"]
    assert lee["emails_from"] == 0 and lee["days_since_heard"] == 120 and not lee["active"]
    assert (m["active_people"], m["known_people"]) == (2, 3)
    # Active first, then by role; people who left go last.
    assert [p["email"] for p in m["people"]] == [DANA, RAVI, AMARA, LEE]


def test_map_is_as_of_its_snapshot() -> None:
    snap = snapshot(contacts=people(), activities=[email_in(at(10), DANA)], as_of=at(20))
    dana = next(p for p in relationship_map(snap)["people"] if p["email"] == DANA)
    assert dana["last_heard_from"] is None and dana["emails_from"] == 0  # that email came later
