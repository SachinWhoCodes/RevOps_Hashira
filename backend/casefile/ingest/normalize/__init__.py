"""Dispatch raw items to the right normaliser, in dependency order within a page."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from casefile.connectors.base import RawItem
from casefile.ingest.context import NormalizeContext
from casefile.ingest.normalize import gcal, gmail, hubspot, usage

Normaliser = Callable[[NormalizeContext, list[RawItem]], None]

# Order matters inside one page: owners → companies → contacts → deals → activities → usage.
ORDER: list[tuple[str, Normaliser]] = [
    ("hubspot.owner", hubspot.owners),
    ("hubspot.company", hubspot.companies),
    ("hubspot.contact", hubspot.contacts),
    ("hubspot.deal", hubspot.deals),
    ("hubspot.note", hubspot.engagements),
    ("hubspot.task", hubspot.engagements),
    ("hubspot.call", hubspot.engagements),
    ("gmail.message", gmail.messages),
    ("gcal.event", gcal.events),
    ("usage.daily", usage.rows),
]
KNOWN = {name for name, _ in ORDER}


def normalize(ctx: NormalizeContext, items: list[RawItem]) -> list[str]:
    """Normalise a page. Returns the object types it did not recognise (logged, not fatal)."""
    grouped: dict[str, list[RawItem]] = defaultdict(list)
    for item in items:
        grouped[item.object_type].append(item)
    for object_type, fn in ORDER:
        if grouped.get(object_type):
            fn(ctx, grouped[object_type])
    ctx.flush()
    return sorted(set(grouped) - KNOWN)
