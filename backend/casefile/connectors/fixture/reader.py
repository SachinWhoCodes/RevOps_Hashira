"""Reads the synthetic company from JSONL files, in dependency order, in source-native shapes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from casefile.connectors.base import ConnectorError, Page, RawItem

# (file stem, object type, external id) — order matters: owners before companies before contacts...
STREAMS: list[tuple[str, Callable[[dict[str, Any]], str], Callable[[dict[str, Any]], str]]] = [
    ("hubspot_owners", lambda r: "hubspot.owner", lambda r: str(r["id"])),
    ("hubspot_companies", lambda r: "hubspot.company", lambda r: str(r["id"])),
    ("hubspot_contacts", lambda r: "hubspot.contact", lambda r: str(r["id"])),
    ("hubspot_deals", lambda r: "hubspot.deal", lambda r: str(r["id"])),
    ("hubspot_engagements", lambda r: f"hubspot.{r['_object']}", lambda r: str(r["id"])),
    ("gmail_messages", lambda r: "gmail.message", lambda r: f"{r['_casefile']['mailbox']}:{r['id']}"),
    ("gcal_events", lambda r: "gcal.event", lambda r: f"{r['_casefile']['calendar']}:{r['id']}"),
    ("usage_daily", lambda r: "usage.daily", lambda r: f"{r['account_key']}:{r['date']}"),
]


class FixtureReader:
    def __init__(self, directory: Path, *, page_size: int = 1000, streams: list[str] | None = None) -> None:
        self.directory = directory
        self.page_size = page_size
        self.streams = streams

    def _manifest_digest(self) -> str:
        manifest = self.directory / "manifest.json"
        if not manifest.exists():
            raise ConnectorError(f"fixture: no manifest.json in {self.directory}", retryable=False)
        return hashlib.sha256(manifest.read_bytes()).hexdigest()

    def pages(self, cursor: dict[str, Any]) -> Iterator[Page]:
        digest = self._manifest_digest()
        state: dict[str, Any] = (
            dict(cursor) if cursor.get("manifest") == digest else {"manifest": digest, "offsets": {}}
        )
        offsets: dict[str, int] = dict(state.get("offsets", {}))
        for stem, object_type, external_id in STREAMS:
            if self.streams is not None and stem not in self.streams:
                continue
            path = self.directory / f"{stem}.jsonl"
            if not path.exists():
                continue
            start = offsets.get(stem, 0)
            read_to = start
            batch: list[RawItem] = []
            with path.open() as fh:
                for line_no, line in enumerate(fh):
                    if line_no < start:
                        continue
                    read_to = line_no + 1
                    row = json.loads(line)
                    batch.append(RawItem(object_type(row), external_id(row), row))
                    if len(batch) == self.page_size:
                        offsets[stem] = read_to
                        yield Page(batch, {"manifest": digest, "offsets": dict(offsets)})
                        batch = []
            offsets[stem] = read_to
            if batch:
                yield Page(batch, {"manifest": digest, "offsets": dict(offsets)})
        # Always end with the final cursor, so it is saved even when nothing new was read.
        yield Page([], {"manifest": digest, "offsets": dict(offsets)})
