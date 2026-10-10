"""What the agent cannot see, per account. Shown on every assessment so a quiet account with no
data is never mistaken for a healthy one."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from casefile.timeline.snapshot import Snapshot


def account_blind_spots(snap: Snapshot, *, ambiguous_records: int = 0) -> list[dict[str, Any]]:
    spots: list[tuple[str, str]] = []
    if "usage" not in snap.sources:
        spots.append(
            ("no_usage_data", "No product usage data, so usage decline and seat signals cannot fire.")
        )
    if not snap.contacts_with_role("champion"):
        spots.append(("no_champion", "No active champion recorded, so champion signals cannot fire."))
    if not snap.contacts_with_role("economic_buyer"):
        spots.append(("no_economic_buyer", "No economic buyer recorded."))
    if snap.account.renewal_date is None:
        spots.append(("no_renewal_date", "No renewal date, so urgency is a default guess."))
    if snap.account.arr is None:
        spots.append(("arr_unknown", "ARR unknown, so the organisation's median ARR is used for priority."))
    if snap.account.seats_purchased is None:
        spots.append(("seats_unknown", "Purchased seats unknown, so seat saturation cannot fire."))
    if not any(a.type == "meeting" for a in snap.activities):
        spots.append(
            ("no_meetings_on_record", "No customer meetings on record (calendar not connected or never met).")
        )
    recent = snap.as_of - timedelta(days=90)
    if not any(a.type == "email" and a.occurred_at >= recent for a in snap.activities):
        spots.append(("no_recent_email", "No customer email in the last 90 days."))
    if ambiguous_records:
        spots.append(
            (
                "unmatched_records",
                f"{ambiguous_records} emails or meetings may belong to this account "
                "but could not be matched with certainty.",
            )
        )
    return [{"code": code, "text": text} for code, text in spots]
