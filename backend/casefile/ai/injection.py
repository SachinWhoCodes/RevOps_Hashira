"""Rule-based prompt-injection detector (plan Phase 3, item 3a). No AI involved; always runs.

A *strong* pattern flags on its own: text addressed to an AI or agent, "ignore previous
instructions", or a fake system/developer note. *Weak* patterns — an instruction to change CRM
data, or to offer a discount — only flag together with a strong pattern, or when they sit in text
a human could not see (hidden HTML, zero-width characters). That keeps ordinary requests such as
"please update the account address" from being flagged.

Flagging never blocks data: it marks the email, quarantines its AI labels (they add no points),
and shows a warning on the case. The real defence is structural — the AI has no write access and
every change needs a person's approval (plan rules R1, R4, R6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from casefile.db.models import Activity, ActivityType, Direction, Org
from casefile.ingest.util import chunks

RULES_VERSION = "inj-rules-3"

STRONG = [
    (
        re.compile(
            r"(?:^|[\n.!?\[(:])\s*(?:dear\s+|hey\s+|hi\s+)?(?:the\s+)?"
            r"(?:ai|a\.i\.|assistant|ai assistant|agent|ai agent|bot|copilot|chatgpt|gpt|claude|llm"
            r"|language model)"
            r"\s*[:,]",
            re.I,
        ),
        "text addressed to an AI assistant",
    ),
    (
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,30}\b(?:previous|prior|above|earlier|all|your|any)\b"
            r"[^.\n]{0,25}\b(?:instructions?|prompts?|rules|guidelines)\b",
            re.I,
        ),
        "asks to ignore previous instructions",
    ),
    (
        re.compile(
            r"\b(?:system|developer|admin(?:istrator)?)\s+(?:note|prompt|message|instruction|override)s?\b"
            r"|\b(?:to|for)\s+(?:all\s+|any\s+)?(?:automated|ai)\s+(?:agents?|systems?|assistants?|tools?)\b"
            r"|[\"']role[\"']\s*:\s*[\"']system[\"']",
            re.I,
        ),
        "fake system or agent instruction",
    ),
    (
        re.compile(
            # Instructions aimed at whatever software reads or summarises the email (rules v2).
            r"\b(?:ai|automated)\s+(?:tools?|systems?|agents?|assistants?|models?)\b[^.\n]{0,30}:"
            r"|\binstructions?\s+(?:for|to)\s+(?:the\s+|your\s+)?(?:summari[sz]er|assistant|agent|ai|model|bot)\b"
            r"|\b(?:when|before|while)\s+you\s+(?:summari[sz]e|classify|process|analy[sz]e|read)\s+this\b"
            r"|\b(?:reads?|process(?:es|ing)?)\s+this\s+(?:inbox|email|message|thread)\s+automatically\b"
            r"|\b(?:the|your)\s+(?:assistant|agent|ai|bot)\s+(?:should|must|needs?\s+to)\b",
            re.I,
        ),
        "instruction aimed at software that reads this email",
    ),
]
WEAK = [
    (
        re.compile(
            r"\b(?:mark|set|change|update|flag|close|move)\b[^.\n]{0,40}\b(?:account|risk|score|health|healthy|"
            r"status|stage|next step|crm|deal)\b",
            re.I,
        ),
        "instruction to change CRM data",
    ),
    (
        re.compile(
            r"\b(?:offer|give|apply|grant)\b[^.\n]{0,30}(?:\d{1,3}\s?%|discount|free months?|credits?)", re.I
        ),
        "instruction to offer a discount",
    ),
]


@dataclass(frozen=True)
class Finding:
    reason: str
    quote: str


def _quote(text: str, start: int, end: int, limit: int = 200) -> str:
    """The sentence or line around a match, trimmed to ``limit`` characters."""
    newline, full_stop = text.rfind("\n", 0, start), text.rfind(". ", 0, start)
    begin = max(newline + 1 if newline != -1 else 0, full_stop + 2 if full_stop != -1 else 0)
    right_candidates = [i for i in (text.find("\n", end), text.find(". ", end)) if i != -1]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    return " ".join(text[begin:right].split())[:limit]


def detect(visible: str, hidden: str = "", zero_width: bool = False) -> Finding | None:
    for pattern, reason in STRONG:
        for where, text in (("", visible), ("hidden text: ", hidden)):
            if text and (m := pattern.search(text)):
                # The pattern may start on punctuation/space before the sentence; skip past it.
                start = m.start()
                while start < m.end() and (text[start] in ".!?[(:" or text[start].isspace()):
                    start += 1
                return Finding(f"{where}{reason}", _quote(text, start, m.end()))
    concealed = hidden or (visible if zero_width else "")
    for pattern, reason in WEAK:
        if concealed and (m := pattern.search(concealed)):
            where = "hidden text: " if hidden else "zero-width characters + "
            return Finding(f"{where}{reason}", _quote(concealed, m.start(), m.end()))
    return None


def scan_injections(session: Session, org: Org) -> set[str]:
    """Run the rules over inbound emails not yet scanned with this rules version.
    Returns the ids of accounts with newly flagged emails."""
    rows = session.execute(
        select(
            Activity.id,
            Activity.account_id,
            Activity.subject,
            Activity.body_text,
            Activity.snippet,
            Activity.extra,
        ).where(
            Activity.org_id == org.id,
            Activity.type == ActivityType.email,
            Activity.direction == Direction.inbound,
            Activity.extra["injection_scanned"].astext.is_distinct_from(RULES_VERSION),
        )
    ).all()
    flagged_accounts: set[str] = set()
    updates = []
    for row in rows:
        extra = dict(row.extra or {})
        visible = f"{row.subject or ''}\n{row.body_text or row.snippet or ''}"
        finding = detect(visible, extra.get("hidden_text", ""), bool(extra.get("zero_width_chars")))
        extra["injection_scanned"] = RULES_VERSION
        values = {"id": row.id, "extra": extra}
        if finding:
            values |= {
                "injection_flag": True,
                "injection_reason": finding.reason,
                "injection_quote": finding.quote,
            }
            if row.account_id:
                flagged_accounts.add(str(row.account_id))
        updates.append(values)
    for batch in chunks(updates, 1000):
        session.execute(update(Activity), batch)
    return flagged_accounts
