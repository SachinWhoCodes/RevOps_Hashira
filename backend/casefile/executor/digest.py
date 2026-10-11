"""Morning digest: one Slack DM per owner at 09:00 local time with their top 3 open cases.

This is the place the plan's "max new cases per owner per day" cap lives: the queue shows
everything ranked; only interruptions are capped. Sent at most once per owner per local day
(checked against the audit log), and only by the executor, which holds the Slack token.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.actions.slack_approval import digest_blocks
from casefile.actions.targets import slack_target
from casefile.audit.log import append as audit
from casefile.db.models import Account, AccountCase, AppUser, Assessment, AuditLog, CaseStatus, Org

TOP_N = 3


class DmSender(Protocol):
    def post_text(self, user_id: str, text: str, blocks: list[dict[str, Any]] | None = None) -> str: ...


@dataclass
class Digest:
    owner: AppUser
    text: str  # plain-text fallback (notifications, clients without blocks)
    case_ids: list[str]
    blocks: list[dict[str, Any]] = field(default_factory=list)


def build_digests(session: Session, org: Org, base_url: str) -> list[Digest]:
    rows = session.execute(
        select(AccountCase, Account, Assessment)
        .join(Account, Account.id == AccountCase.account_id)
        .join(Assessment, Assessment.id == AccountCase.current_assessment_id)
        .where(AccountCase.org_id == org.id, AccountCase.status == CaseStatus.open)
    ).all()

    # Revenue at risk first (by risk priority), then growth (by opportunity priority). An interruption
    # should lead with renewals in danger; expansion cases still appear when an owner has room, and
    # a risk-only ranking (expansion priority is zero) would never show them at all.
    def case_rank(row: tuple[AccountCase, Account, Assessment]) -> tuple[int, float]:
        case, _, a = row
        if case.kind.value == "expansion":
            return (1, -float((a.breakdown or {}).get("opportunity_priority") or 0))
        return (0, -float(a.priority or 0))

    rows = sorted(rows, key=case_rank)
    by_owner: dict[object, list[tuple[AccountCase, Account, Assessment]]] = {}
    for case, account, assessment in rows:
        if case.owner_user_id:
            by_owner.setdefault(case.owner_user_id, []).append((case, account, assessment))
    digests = []
    for owner_id, cases in by_owner.items():
        owner = session.get(AppUser, owner_id)
        if owner is None:
            continue
        top = cases[:TOP_N]
        first = owner.name.split()[0] if owner.name else "there"
        lines = [f"Good morning {first} — your top {len(top)} Casefile cases:"]
        blocks: list[dict[str, Any]] = [{"type": "section", "text": {"type": "mrkdwn", "text": lines[0]}}]
        for i, (case, account, a) in enumerate(top, 1):
            score = a.risk_score if case.kind.value == "risk" else a.opportunity_score
            headline = a.headline or "signals to review"
            link = f"{base_url.rstrip('/')}/accounts/{account.id}"
            line = f"{i}. *{account.name}* — {headline} ({case.kind.value} {score})"
            lines.append(f"{line} <{link}|Open case>")
            # Buttons: "Approve N Tier 1 actions" when a plan is waiting, always "Open in Casefile".
            blocks += digest_blocks(session, case, account, line, link)
        if len(cases) > TOP_N:
            more = f"…and {len(cases) - TOP_N} more in your queue."
            lines.append(more)
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": more}]})
        digests.append(Digest(owner, "\n".join(lines), [str(c.id) for c, _, _ in top], blocks))
    return digests


def _sent_today(session: Session, org: Org, owner: AppUser, day: str) -> bool:
    return (
        session.scalar(
            select(AuditLog.seq)
            .where(
                AuditLog.org_id == org.id,
                AuditLog.action == "digest_sent",
                AuditLog.entity_id == str(owner.id),
                AuditLog.details["day"].astext == day,
            )
            .limit(1)
        )
        is not None
    )


def send_digests(
    session: Session,
    org: Org,
    sender: DmSender,
    *,
    base_url: str,
    now: datetime,
    hour: int = 9,
    force: bool = False,
    owner_email: str | None = None,
) -> list[dict[str, str]]:
    local = now.astimezone(ZoneInfo(org.timezone))
    if not force and local.hour < hour:
        return []
    day = local.date().isoformat()
    sent = []
    for d in build_digests(session, org, base_url):
        if owner_email and d.owner.email != owner_email.lower():
            continue
        if _sent_today(session, org, d.owner, day):
            continue
        target = slack_target(session, org.id, d.owner)
        if target.user_id is None:
            continue
        text, blocks = d.text, list(d.blocks)
        if target.substituted:
            note = f"_Sandbox: this digest was meant for {d.owner.name}._"
            text = f"{note}\n{text}"
            blocks.insert(0, {"type": "context", "elements": [{"type": "mrkdwn", "text": note}]})
        ts = sender.post_text(target.user_id, text, blocks=blocks)
        audit(
            session,
            org.id,
            "digest_sent",
            entity_type="user",
            entity_id=str(d.owner.id),
            details={"day": day, "cases": d.case_ids, "slack_ts": ts, "substituted": target.substituted},
        )
        session.commit()
        sent.append({"owner": d.owner.name, "ts": ts})
    return sent
