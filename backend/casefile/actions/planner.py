"""Planner: turn an open case into a change set a person can approve line by line.

Rule-based (no AI decides anything): the playbook picks the play, code fills every field from
verified claims and current records, and Claude only words the customer email (``drafter``).
For field updates the current value is read from the live CRM now, and the executor will refuse to
write if it has changed by the time the line runs.

One open plan per case: planning again for the same assessment returns the existing plan; a newer
assessment supersedes lines nobody has decided yet.
"""

from __future__ import annotations

import html
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.actions.drafter import DraftContext, draft_email
from casefile.actions.playbooks import Play, choose_play
from casefile.actions.policy import policy_for
from casefile.actions.spec import SPECS, validate_payload
from casefile.actions.targets import gmail_mailbox, hubspot_target, slack_target
from casefile.ai.gateway import AiGateway
from casefile.audit.log import append as audit
from casefile.config import get_settings
from casefile.db.models import (
    Account,
    AccountCase,
    AppUser,
    Assessment,
    CaseKind,
    CaseStatus,
    ChangeItem,
    ChangeItemStatus,
    ChangeSet,
    ChangeSetStatus,
    Deal,
)
from casefile.timeline.snapshot import AccountHistory, ContactView, DealView, Snapshot

OPEN_SET = (ChangeSetStatus.proposed, ChangeSetStatus.partially_approved)
MAX_WHY = 4


class LiveCrm(Protocol):
    """Read-zone access to the CRM's current values (never writes)."""

    def deal_next_step(self, deal_id: str) -> str | None: ...


@dataclass
class PlanResult:
    change_set: ChangeSet | None
    created: bool
    reason: str | None = None
    notes: dict[str, Any] = field(default_factory=dict)


def _business_days_from(start: date, days: int) -> date:
    d = start
    while days > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days -= 1
    return d


def _renewal_deal(snap: Snapshot) -> DealView | None:
    open_renewals = [d for d in snap.deals if d.deal_type == "renewal" and not d.is_closed]
    return min(open_renewals, key=lambda d: d.close_date or date.max) if open_renewals else None


def _recipient(snap: Snapshot, play: Play) -> ContactView | None:
    for role in (play.recipient, play.fallback_recipient):
        if not role:
            continue
        for c in snap.contacts:
            if c.role == role and c.status == "active":
                return c
    return None


def _fill(template: str, values: dict[str, str]) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", value)
    return out


def _date_text(iso: str | None) -> str:
    if not iso:
        return "the renewal"
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%b %Y')}"


def _claims_html(claims: list[dict[str, Any]]) -> str:
    return "<ul>" + "".join(f"<li>{html.escape(c['text'])}</li>" for c in claims[:MAX_WHY]) + "</ul>"


def _case_link(account: Account) -> str:
    return f"{get_settings().app_base_url.rstrip('/')}/accounts/{account.id}"


def _supersede_open_plans(session: Session, case: AccountCase, keep: uuid.UUID | None) -> int:
    count = 0
    for cs in session.scalars(
        select(ChangeSet).where(ChangeSet.case_id == case.id, ChangeSet.status.in_(OPEN_SET))
    ):
        if cs.id == keep:
            continue
        items = list(session.scalars(select(ChangeItem).where(ChangeItem.change_set_id == cs.id)))
        undecided = [i for i in items if i.status in (ChangeItemStatus.proposed, ChangeItemStatus.blocked)]
        for item in undecided:
            item.status = ChangeItemStatus.superseded
        if len(undecided) == len(items):
            cs.status = ChangeSetStatus.superseded
        count += 1
        audit(
            session,
            case.org_id,
            "change_set_superseded",
            entity_type="change_set",
            entity_id=str(cs.id),
            details={"case_id": str(case.id), "undecided_lines": len(undecided)},
        )
    return count


def open_plan(session: Session, case_id: uuid.UUID) -> ChangeSet | None:
    return session.scalar(
        select(ChangeSet)
        .where(ChangeSet.case_id == case_id, ChangeSet.status.in_(OPEN_SET))
        .order_by(ChangeSet.created_at.desc())
        .limit(1)
    )


def plan_case(
    session: Session,
    case_id: uuid.UUID,
    *,
    gateway: AiGateway | None = None,
    live: LiveCrm | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> PlanResult:
    now = now or datetime.now(UTC)
    case = session.get(AccountCase, case_id)
    if case is None or case.status != CaseStatus.open:
        return PlanResult(None, False, "case is not open")
    assessment = session.get(Assessment, case.current_assessment_id) if case.current_assessment_id else None
    if assessment is None:
        return PlanResult(None, False, "case has no assessment yet")
    existing = open_plan(session, case.id)
    if existing is not None and existing.assessment_id == assessment.id and not force:
        return PlanResult(existing, False, "a plan for this assessment already exists")

    account = session.get(Account, case.account_id)
    assert account is not None
    snap = AccountHistory.load(session, account.id).as_of(now)
    if snap is None:
        return PlanResult(None, False, "account has no data as of now")
    owner = session.get(AppUser, case.owner_user_id) if case.owner_user_id else None
    signal_types = {s["type"] for s in (assessment.breakdown or {}).get("signals", [])}
    play = choose_play(case.kind.value, signal_types)
    rules = policy_for(session, case.org_id)
    claims = list(assessment.claims or [])
    why = [{"text": c["text"], "evidence": c.get("evidence", [])} for c in claims[:MAX_WHY]]
    evidence_ids = sorted({e["id"] for c in claims for e in c.get("evidence", [])})

    renewal = _renewal_deal(snap)
    deal = session.get(Deal, renewal.id) if renewal else None
    recipient = _recipient(snap, play)
    renewal_iso = account.renewal_date.isoformat() if account.renewal_date else None
    role_phrase = (play.recipient or "contact").replace("_", " ")
    values = {
        "account": account.name,
        "recipient_name": recipient.name if recipient else f"the {role_phrase}",
        "recipient_first": recipient.name.split()[0] if recipient and recipient.name else "there",
        "owner_name": owner.name if owner else "the account owner",
        "renewal_date": _date_text(renewal_iso),
        "headline": assessment.headline or "signals to review",
    }

    _supersede_open_plans(session, case, keep=None)
    cs = ChangeSet(
        id=uuid.uuid4(),
        org_id=case.org_id,
        case_id=case.id,
        playbook_id=play.id,
        playbook_version=play.version,
        status=ChangeSetStatus.proposed,
        assessment_id=assessment.id,
    )
    notes: dict[str, Any] = {
        "purpose": play.purpose,
        "skipped": [],
        "explanation_status": assessment.status.value,
    }
    session.add(cs)
    session.flush()

    hs = hubspot_target(session, account, deal, owner)
    local_today = snap.local_date(now)
    items: list[ChangeItem] = []
    ai_meta: dict[str, Any] = {}

    def add(
        action: str,
        target_ref: dict[str, Any],
        payload: dict[str, Any],
        *,
        before: dict[str, Any] | None = None,
        blocked: str | None = None,
    ) -> None:
        rule = rules.get(action)
        if rule is None or not rule.enabled:
            notes["skipped"].append({"type": action, "reason": "disabled by policy"})
            return
        spec = SPECS[action]
        item = ChangeItem(
            id=uuid.uuid4(),
            org_id=case.org_id,
            change_set_id=cs.id,
            seq=len(items) + 1,
            action_type=action,
            target_system=spec.target_system,
            target_ref={"account_id": str(account.id), **target_ref},
            before=before or {},
            proposed_payload=validate_payload(action, payload),
            tier=rule.tier,
            evidence_ids=evidence_ids,
            why=why,
            status=ChangeItemStatus.blocked if blocked else ChangeItemStatus.proposed,
            idempotency_key=f"cs:{cs.id}:{len(items) + 1}",
            external_result={"blocked": blocked} if blocked else {},
        )
        items.append(item)
        session.add(item)

    for template in play.actions:
        action = template["type"]
        if action == "review_manually":
            add(action, {}, {"text": _fill(template["text"], values)})
        elif action == "crm_task":
            body = (
                f"<p>{html.escape(play.purpose)}</p><p><strong>Why (verified by Casefile)</strong></p>"
                f"{_claims_html(claims)}<p>Open the case: {html.escape(_case_link(account))}</p>"
            )
            add(
                action,
                hs.ref(),
                {
                    "subject": _fill(template["subject"], values)[:200],
                    "body": body,
                    "due_date": _business_days_from(
                        local_today, int(template.get("due_in_days", 3))
                    ).isoformat(),
                    "priority": template.get("priority", "MEDIUM"),
                },
                blocked=hs.problem,
            )
        elif action == "crm_note":
            risk_or_opp = (
                f"risk score {assessment.risk_score}"
                if case.kind == CaseKind.risk
                else f"opportunity score {assessment.opportunity_score}"
            )
            body = (
                f"<p><strong>Casefile: {html.escape(values['headline'])}</strong></p>{_claims_html(claims)}"
                f"<p>{risk_or_opp} · {len(evidence_ids)} evidence records · play {play.id}. "
                f"Every sentence above was checked against the records it cites.</p>"
                f"<p>Open the case: {html.escape(_case_link(account))}</p>"
            )
            add(action, hs.ref(), {"body": body}, blocked=hs.problem)
        elif action == "crm_next_step":
            if deal is None or renewal is None:
                notes["skipped"].append({"type": action, "reason": "no open renewal deal"})
                continue
            value = _fill(template["value"], values)[:255]
            problem = hs.problem or (None if hs.deal else "the renewal deal has no HubSpot copy")
            current: str | None
            source = "casefile_db"
            if live is not None and hs.deal and not problem:
                current, source = live.deal_next_step(hs.deal), "hubspot_live"
            else:
                current = renewal.next_step or None
            if (current or "").strip() == value:
                notes["skipped"].append({"type": action, "reason": "next step already says this"})
                continue
            before = {"hs_next_step": current, "read_at": now.isoformat(), "source": source}
            add(
                action,
                {**hs.ref(), "property": "hs_next_step"},
                {"value": value},
                before=before,
                blocked=problem,
            )
        elif action == "gmail_draft":
            if recipient is None:
                notes["skipped"].append({"type": action, "reason": f"no active {role_phrase} contact"})
                continue
            mailbox, problem = gmail_mailbox(session, case.org_id)
            ctx = DraftContext(
                sender_name=owner.name if owner else "The account team",
                recipient_name=recipient.name or recipient.email,
                recipient_email=recipient.email,
                recipient_role=recipient.role,
                company=account.name,
                purpose=play.purpose,
                renewal_date=renewal_iso if case.kind == CaseKind.risk else None,
                claims=[c["text"] for c in claims[:MAX_WHY]],
                other_names=[c.name for c in snap.contacts if c.id != recipient.id and c.name]
                + [
                    u.name
                    for u in session.scalars(select(AppUser).where(AppUser.org_id == case.org_id))
                    if u.name
                ],
            )
            # No mailbox to put the draft in: use the template and spend nothing on AI.
            drafted = draft_email(
                gateway if problem is None else None,
                ctx,
                expansion=case.kind == CaseKind.expansion,
                account_id=account.id,
            )
            ai_meta = {
                "draft_source": drafted.source,
                "draft_reasons": drafted.reasons,
                "draft_cost_usd": round(drafted.cost_usd, 6),
                "model": drafted.model,
                "prompt_version": drafted.prompt_version,
            }
            add(
                action,
                {"mailbox": mailbox, "contact_id": str(recipient.id), "recipient_role": recipient.role},
                {"to": [recipient.email], "subject": drafted.subject, "body": drafted.body},
                blocked=drafted.blocked_reason or problem,
            )
        elif action == "slack_dm":
            st = slack_target(session, case.org_id, owner)
            planned = [SPECS[i.action_type].label for i in items if i.status != ChangeItemStatus.blocked]
            lines = []
            if st.substituted:
                lines.append(
                    f"_Sandbox: this message was meant for {st.intended_for or 'the account owner'}._"
                )
            score = assessment.risk_score if case.kind == CaseKind.risk else assessment.opportunity_score
            lines.append(f"*{account.name}* — {values['headline']} ({case.kind.value} score {score})")
            lines += [f"• {c['text']}" for c in claims[:3]]
            if planned:
                lines.append(f"Casefile prepared: {', '.join(planned)}.")
            lines.append(f"<{_case_link(account)}|Open the case in Casefile>")
            add(
                action,
                {"user_id": st.user_id, "intended_for": st.intended_for, "substituted": st.substituted},
                {"text": "\n".join(lines)},
                blocked=st.problem,
            )
    if not items:
        add("review_manually", {}, {"text": _fill("Review {account}: {headline}", values)})
    cs.notes = {**notes, **ai_meta}  # assigned, not mutated, so the JSONB change is saved
    session.flush()
    audit(
        session,
        case.org_id,
        "change_set_proposed",
        actor_type="agent",
        entity_type="change_set",
        entity_id=str(cs.id),
        evidence_ids=evidence_ids,
        details={
            "case_id": str(case.id),
            "assessment_id": str(assessment.id),
            "play": play.id,
            "playbook_version": play.version,
            "items": [
                {"seq": i.seq, "type": i.action_type, "tier": i.tier, "status": i.status.value} for i in items
            ],
            "skipped": notes["skipped"],
            "draft_source": ai_meta.get("draft_source"),
        },
        model=ai_meta.get("model"),
        prompt_version=ai_meta.get("prompt_version"),
    )
    return PlanResult(cs, True, notes=cs.notes)
