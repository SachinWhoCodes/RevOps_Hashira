"""Morning digest: one Slack DM per owner at 09:00 local time with their top 3 open cases.

This is the place the plan's "max new cases per owner per day" cap lives: the queue shows
everything ranked; only interruptions are capped. Sent at most once per owner per local day
(checked against the audit log), and only by the executor, which holds the Slack token.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.actions.targets import slack_target
from casefile.audit.log import append as audit
from casefile.db.models import Account, AccountCase, AppUser, Assessment, AuditLog, CaseStatus, Org

TOP_N = 3


class DmSender(Protocol):
    def post_text(self, user_id: str, text: str) -> str: ...


@dataclass
class Digest:
    owner: AppUser
    text: str
    case_ids: list[str]


def build_digests(session: Session, org: Org, base_url: str) -> list[Digest]:
    rows = session.execute(
        select(AccountCase, Account, Assessment)
        .join(Account, Account.id == AccountCase.account_id)
        .join(Assessment, Assessment.id == AccountCase.current_assessment_id)
        .where(AccountCase.org_id == org.id, AccountCase.status == CaseStatus.open)
        .order_by(Assessment.priority.desc())
    ).all()
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
        for i, (case, account, a) in enumerate(top, 1):
            score = a.risk_score if case.kind.value == "risk" else a.opportunity_score
            headline = a.headline or "signals to review"
            link = f"{base_url.rstrip('/')}/accounts/{account.id}"
            lines.append(f"{i}. *{account.name}* — {headline} ({case.kind.value} {score}) <{link}|Open case>")
        if len(cases) > TOP_N:
            lines.append(f"…and {len(cases) - TOP_N} more in your queue.")
        digests.append(Digest(owner, "\n".join(lines), [str(c.id) for c, _, _ in top]))
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
) -> list[dict[str, str]]:
    local = now.astimezone(ZoneInfo(org.timezone))
    if not force and local.hour < hour:
        return []
    day = local.date().isoformat()
    sent = []
    for d in build_digests(session, org, base_url):
        if _sent_today(session, org, d.owner, day):
            continue
        target = slack_target(session, org.id, d.owner)
        if target.user_id is None:
            continue
        text = d.text
        if target.substituted:
            text = f"_Sandbox: this digest was meant for {d.owner.name}._\n" + text
        ts = sender.post_text(target.user_id, text)
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
