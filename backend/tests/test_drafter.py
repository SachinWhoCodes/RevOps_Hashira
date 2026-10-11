"""Draft checker: real Claude drafts that must pass, and invented names / money talk that must not."""
# ruff: noqa: E501  (verbatim model output kept as written)

from __future__ import annotations

import pytest

from casefile.actions.drafter import DraftContext, check_draft, money_talk, template_draft

CTX = DraftContext(
    "Rahul Verma",
    "Mei Tanaka",
    "mei@lattice.test",
    "economic_buyer",
    "Lattice Logistics",
    "reach the economic buyer",
    "2026-12-16",
    other_names=["Pooja Kapoor", "Priya Sharma", "Rahul Verma"],
)

# Two drafts Claude Opus 5.5 actually wrote (2026-10-11). An earlier, stricter checker rejected both
# over the plain words "Introducing" and "Account" and fell back to the template.
REAL = [
    (
        "Introducing myself ahead of your renewal",
        "Hi Mei,\n\nI'm Rahul, the account owner for Lattice Logistics. I understand your previous main "
        "contact has moved on, and I was pointed to you as the right person to stay in touch with. I'd like "
        "to introduce myself and make sure your team has the support it needs ahead of the renewal on "
        "2026-12-16.\n\nWould you have 30 minutes for a call next week? I'd like to hear how things are going "
        "for your team, who I should work with day to day, and anything you'd like to see from us. If another "
        "time suits you better, just let me know.\n\nBest,\nRahul",
    ),
    (
        "Account owner introduction for Lattice Logistics",
        "Hi Mei,\n\nI'm Rahul Verma, the account owner for Lattice Logistics. I understand your previous main "
        "contact has moved on, and I'd like to make sure your team has what it needs ahead of the renewal on "
        "2026-12-16.\n\nWould you have 30 minutes for a call next week? I'd like to hear how things are going "
        "for your team, answer any questions, and agree on who I should stay in touch with going forward. If "
        "someone else on your side is better placed for this, I'm happy to speak with them instead.\n\n"
        "Thanks,\nRahul",
    ),
]


@pytest.mark.parametrize(("subject", "body"), REAL)
def test_real_drafts_pass(subject: str, body: str) -> None:
    assert check_draft(subject, body, CTX) == []
    assert money_talk(subject + body) is None


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("Rahul from Datawise mentioned you.", "Datawise"),
        ("Pooja has moved on, so I wanted to reach out.", "Pooja"),
        ("I spoke with Kavya Sen last week.", "Kavya"),
        ("Kavya mentioned the team is busy.", "Kavya"),
        ("Usage fell 35% this month.", "number"),
        ("We noticed your usage dropped.", "internal"),
        ("We guarantee the rollout will work.", "promise"),
    ],
)
def test_bad_drafts_are_caught(body: str, fragment: str) -> None:
    reasons = check_draft("Hello", f"Hi Mei,\n\n{body}\n\nBest,\nRahul", CTX)
    assert any(fragment.lower() in r.lower() for r in reasons), reasons


@pytest.mark.parametrize(
    "text", ["We can offer a 20% discount", "ahead of the contract renewal", "a free month"]
)
def test_money_talk(text: str) -> None:
    assert money_talk(text)


def test_templates_pass_and_use_a_readable_date() -> None:
    for expansion in (False, True):
        subject, body = template_draft(CTX, expansion=expansion)
        assert check_draft(subject, body, CTX) == []
    assert "16 Dec 2026" in template_draft(CTX, expansion=False)[1]
