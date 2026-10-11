"""Email reader (Claude Haiku 5.5): labels what a customer email says, and spots injection attempts.

Trust rules (plan Phase 3, item 2):
- Every label must come with a quote that appears verbatim in the text we sent; otherwise it is
  dropped. The model cannot invent evidence.
- Confidence below 0.7 makes a label "possible": shown, but it adds no points.
- Labels from an email flagged as an injection attempt are "quarantined": shown, never scored.
- A refusal or an API failure leaves the email unlabelled; it never blocks the pipeline.

Only inbound, human (not auto-reply, not bounce) customer emails are read: they carry the
customer's voice, and limiting the input keeps cost low.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from casefile.ai.gateway import AiGateway, AiResult, load_prompt
from casefile.ai.masking import mask
from casefile.db.models import Activity, ActivityType, Contact, Deal, Direction, EmailLabel, LabelStatus, Org

ACCEPT_AT = 0.7
MAX_BODY_CHARS = 6000  # long threads are cut, never silently: the cut is marked in the text

LabelName = Literal[
    "complaint",
    "frustration",
    "competitor_mention",
    "upgrade_interest",
    "pricing_question",
    "seat_request",
    "champion_left",
    "out_of_office",
]


class LabelOut(BaseModel):
    label: LabelName
    confidence: float
    quote: str


class InjectionOut(BaseModel):
    suspected: bool
    quote: str


class EmailReading(BaseModel):
    labels: list[LabelOut]
    injection: InjectionOut


def _norm(text: str) -> str:
    return " ".join(text.split())


def email_content(act: Activity, sender_role: str) -> str:
    body = act.body_text or act.snippet or ""
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "\n[... email truncated for length ...]"
    parts = [
        f'<customer_email id="{act.id}" untrusted="true">',
        f"Subject: {act.subject or ''}",
        f"From: {sender_role}",
        f"Date: {act.occurred_at.date().isoformat()}",
        "",
        body,
        "</customer_email>",
    ]
    hidden = (act.extra or {}).get("hidden_text")
    if hidden:
        parts += ['<hidden_text untrusted="true">', hidden, "</hidden_text>"]
    return "\n".join(parts)


@dataclass
class ReadResult:
    read: int = 0
    labels_accepted: int = 0
    labels_possible: int = 0
    labels_quarantined: int = 0
    labels_dropped_unverified: int = 0
    flagged_injection: int = 0
    failed: dict[str, int] = field(default_factory=dict)
    accounts: set[uuid.UUID] = field(default_factory=set)


def _pending_filter(org: Org, prompt_version: str, since_days: int) -> list[Any]:
    since = datetime.now(UTC) - timedelta(days=since_days)
    return [
        Activity.org_id == org.id,
        Activity.type == ActivityType.email,
        Activity.direction == Direction.inbound,
        Activity.account_id.is_not(None),
        # Customers only (a company with a deal): mail from prospects, vendors and newsletters is never read.
        exists().where(Deal.account_id == Activity.account_id),
        Activity.occurred_at >= since,
        Activity.extra["auto_reply"].astext.is_distinct_from("true"),
        Activity.extra["bounce"].astext.is_distinct_from("true"),
        Activity.extra["ai_read"]["prompt"].astext.is_distinct_from(prompt_version),
    ]


def count_pending_emails(session: Session, org: Org, prompt_version: str, *, since_days: int = 120) -> int:
    where = _pending_filter(org, prompt_version, since_days)
    return int(session.scalar(select(func.count()).select_from(Activity).where(*where)) or 0)


def pending_emails(
    session: Session, org: Org, prompt_version: str, *, since_days: int = 120, limit: int | None = None
) -> list[Activity]:
    query = (
        select(Activity)
        .where(*_pending_filter(org, prompt_version, since_days))
        .order_by(Activity.occurred_at.desc())
    )
    if limit:
        query = query.limit(limit)
    return list(session.scalars(query))


def _sender_role(session: Session, act: Activity) -> str:
    contact = session.scalar(
        select(Contact).where(Contact.org_id == act.org_id, Contact.email == act.from_email)
    )
    return f"customer contact ({contact.role.value})" if contact else "customer contact"


def apply_reading(
    session: Session, act: Activity, sent: str, result: AiResult[EmailReading], out: ReadResult
) -> None:
    reading = result.output
    assert reading is not None
    version = result.prompt_version or ""
    haystack = _norm(sent)
    extra = dict(act.extra or {})
    if reading.injection.suspected:
        quote = reading.injection.quote.strip()
        verified = bool(quote) and _norm(quote) in haystack
        if not act.injection_flag:
            act.injection_flag = True
            act.injection_reason = "AI reader: instruction-like text aimed at software" + (
                "" if verified else " (quote not found verbatim)"
            )
            act.injection_quote = quote[:500] if verified else None
        out.flagged_injection += 1
    session.execute(
        delete(EmailLabel).where(EmailLabel.activity_id == act.id, EmailLabel.prompt_version == version)
    )
    dropped = 0
    for label in reading.labels:
        if not label.quote.strip() or _norm(label.quote) not in haystack:
            dropped += 1  # the model cannot cite text that is not in the email
            continue
        confidence = max(0.0, min(1.0, label.confidence))
        if act.injection_flag:
            status = LabelStatus.quarantined
            out.labels_quarantined += 1
        elif confidence >= ACCEPT_AT:
            status = LabelStatus.accepted
            out.labels_accepted += 1
        else:
            status = LabelStatus.possible
            out.labels_possible += 1
        session.add(
            EmailLabel(
                org_id=act.org_id,
                activity_id=act.id,
                label=label.label,
                confidence=confidence,
                quote=label.quote.strip()[:500],
                status=status,
                model=result.model or "",
                prompt_version=version,
            )
        )
    out.labels_dropped_unverified += dropped
    extra["ai_read"] = {
        "prompt": version,
        "model": result.model,
        "status": "ok",
        "dropped": dropped,
        "at": datetime.now(UTC).isoformat(),
    }
    act.extra = extra
    out.read += 1
    if act.account_id:
        out.accounts.add(act.account_id)


def _mark_unreadable(act: Activity, version: str, error: str) -> None:
    # A refusal is final for this prompt version; transient failures are retried next run.
    if error.startswith("refusal"):
        act.extra = {**(act.extra or {}), "ai_read": {"prompt": version, "status": "refused"}}


def read_emails(session: Session, org: Org, gateway: AiGateway, emails: list[Activity]) -> ReadResult:
    system, version = load_prompt("email_reader")
    out = ReadResult()
    for act in emails:
        content = email_content(act, _sender_role(session, act))
        result = gateway.structured(
            purpose="email_reader",
            role="reader",
            system=system,
            content=content,
            schema=EmailReading,
            prompt_version=version,
            account_id=act.account_id,
        )
        if not result.ok or result.output is None:
            out.failed[result.error or "unknown"] = out.failed.get(result.error or "unknown", 0) + 1
            _mark_unreadable(act, version, result.error or "")
            if result.error in {"budget_exceeded", "ai_disabled", "no_api_key"}:
                break
            continue
        apply_reading(session, act, mask(content), result, out)
        session.flush()
    return out


def read_emails_batch(
    session: Session, org: Org, gateway: AiGateway, emails: list[Activity], *, poll_seconds: float = 30
) -> ReadResult:
    """Same as ``read_emails`` through the Message Batches API (half price; minutes to hours)."""
    system, version = load_prompt("email_reader")
    contents = {str(a.id): email_content(a, _sender_role(session, a)) for a in emails}
    batch_id, ready, keys = gateway.submit_batch(
        purpose="email_reader",
        role="reader",
        system=system,
        items=list(contents.items()),
        schema=EmailReading,
        prompt_version=version,
    )
    session.commit()
    if batch_id:
        ready |= gateway.collect_batch(
            batch_id,
            purpose="email_reader",
            schema=EmailReading,
            prompt_version=version,
            keys=keys,
            poll_seconds=poll_seconds,
        )
    out = ReadResult()
    by_id = {str(a.id): a for a in emails}
    for cid, result in ready.items():
        act = by_id[cid]
        if not result.ok or result.output is None:
            out.failed[result.error or "unknown"] = out.failed.get(result.error or "unknown", 0) + 1
            _mark_unreadable(act, version, result.error or "")
            continue
        result.prompt_version = version
        apply_reading(session, act, mask(contents[cid]), result, out)
    session.flush()
    return out
