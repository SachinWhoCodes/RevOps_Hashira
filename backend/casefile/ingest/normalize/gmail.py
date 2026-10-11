"""Gmail ``users.messages.get`` (format=full) → email activities.

- One activity per RFC Message-ID, so a message that sits in two owners' mailboxes is stored once.
- Direction comes from our own (internal) domains.
- Auto-replies and bounces are detected from headers; a bounce's participant is the address that
  failed, so it links to the right customer.
- Full body text is retention-limited (``body_purge_after``); snippets are kept as evidence.
"""

from __future__ import annotations

import base64
import re
from datetime import timedelta
from email.utils import getaddresses
from typing import Any

from sqlalchemy.dialects.postgresql import insert

from casefile.connectors.base import RawItem
from casefile.db.models import Activity, ActivityType, Direction, MatchMethod
from casefile.ingest.context import NormalizeContext
from casefile.ingest.text import html_to_text, snippet, strip_zero_width
from casefile.ingest.util import chunks, is_ours, is_system_address, parse_ts

SOURCE = "gmail"
BODY_RETENTION = timedelta(days=90)
AUTO_REPLY_SUBJECT = re.compile(r"^(automatic reply|auto:|out of (the )?office|autoreply)", re.I)
BOUNCE_SUBJECT = re.compile(
    r"(delivery status notification|undeliver|mail delivery failed|returned mail)", re.I
)
FAILED_RECIPIENT = re.compile(r"delivered to\s+<?([\w.+'-]+@[\w.-]+\.\w+)>?", re.I)


def _headers(payload: dict[str, Any]) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in payload.get("headers") or []}


def _decode(data: str | None) -> str:
    if not data:
        return ""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _bodies(part: dict[str, Any]) -> tuple[str, str]:
    """(text/plain, text/html) from a MIME tree; first of each wins."""
    plain, html = "", ""
    mime = part.get("mimeType", "")
    if mime == "text/plain":
        plain = _decode((part.get("body") or {}).get("data"))
    elif mime == "text/html":
        html = _decode((part.get("body") or {}).get("data"))
    for child in part.get("parts") or []:
        p, h = _bodies(child)
        plain, html = plain or p, html or h
    return plain, html


def _addresses(value: str | None) -> list[str]:
    return [addr.lower() for _, addr in getaddresses([value or ""]) if "@" in addr]


def parse_message(
    raw: dict[str, Any],
    internal_domains: set[str],
    internal_addresses: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any] | None:
    payload = raw.get("payload") or {}
    hdr = _headers(payload)
    sent_at = parse_ts(raw.get("internalDate")) or parse_ts(hdr.get("date"))
    if sent_at is None:
        return None
    sender = (_addresses(hdr.get("from")) or [""])[0]
    to, cc = _addresses(hdr.get("to")), _addresses(hdr.get("cc"))
    plain, html = _bodies(payload)
    hidden = ""
    if html:
        visible_html, hidden = html_to_text(html)
        body = plain or visible_html
    else:
        body = plain
    body, had_zero_width = strip_zero_width(body)
    subject = hdr.get("subject", "")

    auto_submitted = hdr.get("auto-submitted", "no").lower() != "no"
    is_bounce = is_system_address(sender) and bool(BOUNCE_SUBJECT.search(subject))
    is_auto_reply = not is_bounce and (auto_submitted or bool(AUTO_REPLY_SUBJECT.search(subject)))
    failed: str | None = None
    if is_bounce:
        failed_list = _addresses(hdr.get("x-failed-recipients"))
        failed = failed_list[0] if failed_list else None
        if failed is None and (m := FAILED_RECIPIENT.search(body)):
            failed = m.group(1).lower()

    mailbox_owner = str((raw.get("_casefile") or {}).get("mailbox", "")).lower()
    ours = {*internal_addresses, mailbox_owner} - {""}

    def internal(addr: str) -> bool:
        return is_ours(addr, internal_domains, ours)

    if is_bounce:
        participants = [failed] if failed else []
        direction = Direction.inbound
    else:
        participants = list(dict.fromkeys(a for a in [sender, *to, *cc] if a and not is_system_address(a)))
        if internal(sender) and all(internal(a) for a in to + cc):
            direction = Direction.internal
        elif internal(sender):
            direction = Direction.outbound
        else:
            direction = Direction.inbound

    message_id = (hdr.get("message-id") or "").strip() or f"gmail:{raw['id']}"
    mailbox = (raw.get("_casefile") or {}).get("mailbox", "")
    extra: dict[str, Any] = {
        "mailbox": mailbox,
        "gmail_id": raw.get("id"),
        "labels": raw.get("labelIds") or [],
        "auto_reply": is_auto_reply,
        "bounce": is_bounce,
    }
    if failed:
        extra["failed_recipient"] = failed
    if hidden or had_zero_width:
        extra["hidden_text"] = hidden[:2000]
        extra["zero_width_chars"] = had_zero_width
    if hdr.get("in-reply-to"):
        extra["in_reply_to"] = hdr["in-reply-to"]
    return {
        "type": ActivityType.email,
        "direction": direction,
        "occurred_at": sent_at,
        "known_at": sent_at,
        "source": SOURCE,
        "source_id": message_id,
        "dedupe_key": message_id,
        "thread_id": f"{mailbox}:{raw.get('threadId')}" if raw.get("threadId") else None,
        "subject": subject,
        "snippet": snippet(body),
        "body_text": body,
        "from_email": sender or None,
        "to_emails": to,
        "cc_emails": cc,
        "participant_emails": participants,
        "extra": extra,
        "match_method": MatchMethod.unmatched,
        "match_confidence": 0.0,
    }


def messages(ctx: NormalizeContext, items: list[RawItem]) -> None:
    internal = ctx.internal_domains
    purge_after = ctx.now() + BODY_RETENTION
    rows = []
    for item in items:
        parsed = parse_message(item.payload, internal, ctx.internal_addresses)
        if parsed is None:
            continue
        if parsed["direction"] == Direction.internal:
            parsed["match_method"] = MatchMethod.internal_only
        parsed.update(org_id=ctx.org_id, body_purge_after=purge_after)
        rows.append(parsed)
    for batch in chunks(rows, 500):
        # First copy of a Message-ID wins; Gmail messages never change after delivery.
        ctx.session.execute(
            insert(Activity)
            .values(batch)
            .on_conflict_do_nothing(index_elements=["org_id", "source", "source_id"])
        )
