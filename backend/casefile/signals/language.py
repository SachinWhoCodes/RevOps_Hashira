"""Signals from what customers write, read by the AI email reader.

Only *accepted* labels count: the quote was found verbatim in the email, confidence was at least
0.7, and the email was not flagged as an injection attempt. "Possible" and "quarantined" labels
are shown to people but never add points.
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any

from casefile.db.models import Polarity
from casefile.signals.base import Evidence, SignalHit, day_label, iso, signal
from casefile.timeline.snapshot import ActivityView, LabelView, Snapshot

NEGATIVE = {
    "complaint": "complaint",
    "frustration": "frustration",
    "competitor_mention": "competitor mention",
}
BUYING = {
    "upgrade_interest": "upgrade interest",
    "pricing_question": "pricing question",
    "seat_request": "seat request",
}


def _labelled(
    snap: Snapshot, names: dict[str, str], lookback_days: int
) -> list[tuple[ActivityView, LabelView]]:
    since = snap.as_of - timedelta(days=lookback_days)
    return [
        (a, lab)
        for a in snap.activities
        if a.type == "email" and a.direction == "inbound" and since <= a.occurred_at <= snap.as_of
        for lab in a.labels
        if lab.label in names and lab.status == "accepted"
    ]


def _hit(
    snap: Snapshot,
    kind: str,
    polarity: Polarity,
    names: dict[str, str],
    found: list[tuple[ActivityView, LabelView]],
    lead: str,
) -> list[SignalHit]:
    emails = {a.id: a for a, _ in found}
    first = min(snap.local_date(a.occurred_at) for a in emails.values())
    latest = max(snap.local_date(a.occurred_at) for a in emails.values())
    counts = Counter(lab.label for _, lab in found)
    what = ", ".join(f"{names[k]} ({n})" if n > 1 else names[k] for k, n in sorted(counts.items()))
    n = len(emails)
    summary = f"{lead} {n} customer email{'s' if n > 1 else ''} since {day_label(first)}: {what}."
    seen: set[Any] = set()
    evidence: list[Evidence] = []
    for a, lab in sorted(found, key=lambda f: f[0].occurred_at, reverse=True):
        if a.id in seen or len(evidence) == 5:
            continue
        seen.add(a.id)
        quote = lab.quote if len(lab.quote) <= 90 else lab.quote[:87] + "..."
        evidence.append(
            Evidence(
                "activity",
                str(a.id),
                f'{names[lab.label].capitalize()}: "{quote}" ({day_label(snap.local_date(a.occurred_at))})',
                iso(a.occurred_at),
            )
        )
    facts: dict[str, Any] = {
        "emails": n,
        "first_email_date": first.isoformat(),
        "latest_email_date": latest.isoformat(),
        **{f"{k}_count": counts.get(k, 0) for k in names},
    }
    return [SignalHit(kind, polarity, n, summary, facts, tuple(evidence))]


@signal(
    "negative_tone",
    Polarity.risk,
    "email+ai",
    "Complaint, frustration or competitor mention (AI-read, quote-verified)",
)
def negative_tone(snap: Snapshot, cfg: dict[str, Any]) -> list[SignalHit]:
    found = _labelled(snap, NEGATIVE, cfg["lookback_days"])
    return _hit(snap, "negative_tone", Polarity.risk, NEGATIVE, found, "Negative tone in") if found else []


@signal(
    "upgrade_interest",
    Polarity.opportunity,
    "email+ai",
    "Upgrade interest, pricing question or seat request (AI-read)",
)
def upgrade_interest(snap: Snapshot, cfg: dict[str, Any]) -> list[SignalHit]:
    found = _labelled(snap, BUYING, cfg["lookback_days"])
    return (
        _hit(snap, "upgrade_interest", Polarity.opportunity, BUYING, found, "Buying interest in")
        if found
        else []
    )
