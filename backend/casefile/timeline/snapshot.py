"""As-of snapshots — the only door analysis code has to account history (plan rule R3).

``AccountHistory.load`` reads everything stored about one account once; ``history.as_of(t)``
returns a frozen ``Snapshot`` containing only what was knowable at ``t``:

- entity field values: the latest ``field_history`` row with ``valid_from <= t``
- entities themselves: only those that existed at ``t``
- activities: ``known_at <= t`` (a meeting booked before ``t`` for after ``t`` is visible as
  scheduled); cancelled only if ``cancelled_at <= t``
- usage: days strictly before ``t``'s calendar day (usage for a day lands after it ends)

Live scoring calls ``as_of(now)``; the back-test calls the same code with past dates.

Known, accepted approximations (documented for the back-test report):
- Which account an email belongs to is decided with today's identity knowledge. Identity is about
  who a person is, not about the outcome, so it does not leak the label.
- A rescheduled meeting keeps only its latest time (Calendar does not give reschedule history).
"""

from __future__ import annotations

import bisect
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.db.models import (
    Account,
    Activity,
    AppUser,
    Contact,
    Deal,
    EmailLabel,
    FieldHistory,
    MatchMethod,
    Org,
    UsageDaily,
)
from casefile.ingest.util import field_mapping

UNRESOLVED = {MatchMethod.unmatched, MatchMethod.ambiguous, MatchMethod.internal_only}


@dataclass(frozen=True)
class AccountView:
    id: uuid.UUID
    name: str
    domains: tuple[str, ...]
    owner_user_id: uuid.UUID | None
    seats_purchased: int | None
    renewal_date: date | None
    arr: Decimal | None
    renewal_deal_id: uuid.UUID | None


@dataclass(frozen=True)
class ContactView:
    id: uuid.UUID
    email: str
    name: str
    title: str | None
    role: str
    status: str
    origin: str
    status_since: datetime | None = None  # when the current status was recorded


@dataclass(frozen=True)
class DealView:
    id: uuid.UUID
    name: str
    deal_type: str
    stage: str | None
    is_closed: bool
    is_won: bool
    amount: Decimal | None
    close_date: date | None
    next_step: str
    next_step_since: datetime | None  # when the current next-step value was set
    next_step_ever_set: bool = False  # had a non-empty next step at some point up to as_of
    next_step_date: date | None = None


@dataclass(frozen=True)
class LabelView:
    label: str
    status: str  # accepted | possible | quarantined
    confidence: float
    quote: str


@dataclass(frozen=True)
class ActivityView:
    id: uuid.UUID
    type: str
    direction: str
    source: str
    occurred_at: datetime
    known_at: datetime
    cancelled: bool
    subject: str | None
    snippet: str | None
    from_email: str | None
    participant_emails: tuple[str, ...]
    contact_ids: tuple[uuid.UUID, ...]
    thread_id: str | None
    extra: dict[str, Any]
    injection_flag: bool
    match_method: str
    match_confidence: float
    labels: tuple[LabelView, ...] = ()
    injection_reason: str | None = None
    injection_quote: str | None = None


@dataclass(frozen=True)
class UsageDay:
    date: date
    active_users: int
    key_actions: int
    seats_used: int | None
    departments: dict[str, Any]


@dataclass(frozen=True)
class Snapshot:
    as_of: datetime
    tz: ZoneInfo
    account: AccountView
    contacts: tuple[ContactView, ...]
    deals: tuple[DealView, ...]
    activities: tuple[ActivityView, ...]  # sorted by occurred_at; may include scheduled future meetings
    usage: tuple[UsageDay, ...]  # sorted by date
    sources: frozenset[str]  # sources that had any data for this account by as_of

    @property
    def today(self) -> date:
        return self.as_of.astimezone(self.tz).date()

    def contact(self, contact_id: uuid.UUID) -> ContactView | None:
        return next((c for c in self.contacts if c.id == contact_id), None)

    def renewal_deal(self) -> DealView | None:
        return next((d for d in self.deals if d.id == self.account.renewal_deal_id), None)

    def days_to_renewal(self) -> int | None:
        return (self.account.renewal_date - self.today).days if self.account.renewal_date else None

    def local_date(self, dt: datetime) -> date:
        return dt.astimezone(self.tz).date()

    def is_churned(self) -> bool:
        """No open renewal deal, and the most recent closed renewal deal was lost."""
        renewals = [d for d in self.deals if d.deal_type == "renewal"]
        if any(not d.is_closed for d in renewals):
            return False
        closed = sorted((d for d in renewals if d.close_date), key=lambda d: d.close_date or date.min)
        return bool(closed) and not closed[-1].is_won

    def contacts_with_role(self, role: str, *, active_only: bool = True) -> list[ContactView]:
        return [c for c in self.contacts if c.role == role and (c.status == "active" or not active_only)]


class _Fields:
    """Per-entity field history, searchable by time."""

    def __init__(self) -> None:
        self._data: dict[tuple[uuid.UUID, str], tuple[list[datetime], list[Any]]] = {}

    def add(self, entity_id: uuid.UUID, name: str, at: datetime, value: Any) -> None:
        times, values = self._data.setdefault((entity_id, name), ([], []))
        times.append(at)
        values.append(value)

    def value(self, entity_id: uuid.UUID, name: str, t: datetime, default: Any = None) -> Any:
        entry = self._data.get((entity_id, name))
        if not entry:
            return default
        i = bisect.bisect_right(entry[0], t)
        return entry[1][i - 1] if i else default

    def since(self, entity_id: uuid.UUID, name: str, t: datetime) -> datetime | None:
        entry = self._data.get((entity_id, name))
        if not entry:
            return None
        i = bisect.bisect_right(entry[0], t)
        return entry[0][i - 1] if i else None

    def values_until(self, entity_id: uuid.UUID, name: str, t: datetime) -> list[Any]:
        entry = self._data.get((entity_id, name))
        if not entry:
            return []
        return entry[1][: bisect.bisect_right(entry[0], t)]

    def changes(self, t: datetime) -> list[tuple[uuid.UUID, str, datetime, Any]]:
        return [
            (eid, name, at, value)
            for (eid, name), (times, values) in self._data.items()
            for at, value in zip(times, values, strict=True)
            if at <= t
        ]


def _dec(v: Any) -> Decimal | None:
    return Decimal(str(v)) if v not in (None, "") else None


def _day(v: Any) -> date | None:
    return date.fromisoformat(v[:10]) if isinstance(v, str) and v else None


class AccountHistory:
    def __init__(self) -> None:
        self.account: Account
        self.tz: ZoneInfo
        self.fields = _Fields()
        self.contacts: list[Contact] = []
        self.deals: list[Deal] = []
        self.activities: list[Activity] = []
        self.usage: list[UsageDaily] = []
        self.owner_users: dict[str, uuid.UUID] = {}
        self.labels: dict[uuid.UUID, list[LabelView]] = {}
        self.won: set[str] = set()
        self.lost: set[str] = set()

    @classmethod
    def load(cls, session: Session, account_id: uuid.UUID) -> AccountHistory:
        h = cls()
        account = session.get(Account, account_id)
        if account is None:
            raise LookupError(f"account {account_id} not found")
        h.account = account
        org = session.get(Org, account.org_id)
        assert org is not None
        h.tz = ZoneInfo(org.timezone)
        dmap = field_mapping()["hubspot"]["deal"]
        h.won, h.lost = set(dmap["won_stages"]), set(dmap["lost_stages"])
        h.contacts = list(session.scalars(select(Contact).where(Contact.account_id == account_id)))
        h.deals = list(session.scalars(select(Deal).where(Deal.account_id == account_id)))
        h.activities = list(
            session.scalars(
                select(Activity)
                .where(Activity.account_id == account_id, Activity.match_method.not_in(UNRESOLVED))
                .order_by(Activity.occurred_at)
            )
        )
        # AI labels from the prompt version that last read each email. The label describes content
        # that existed when the email arrived, so using it in a past snapshot leaks nothing.
        read_version = {a.id: ((a.extra or {}).get("ai_read") or {}).get("prompt") for a in h.activities}
        email_ids = [i for i, v in read_version.items() if v]
        for start in range(0, len(email_ids), 1000):
            for lab in session.scalars(
                select(EmailLabel).where(EmailLabel.activity_id.in_(email_ids[start : start + 1000]))
            ):
                if lab.prompt_version == read_version.get(lab.activity_id):
                    h.labels.setdefault(lab.activity_id, []).append(
                        LabelView(lab.label, lab.status.value, lab.confidence, lab.quote or "")
                    )
        h.usage = list(
            session.scalars(
                select(UsageDaily).where(UsageDaily.account_id == account_id).order_by(UsageDaily.date)
            )
        )
        h.owner_users = {
            str(o): uid
            for o, uid in session.execute(
                select(AppUser.crm_owner_id, AppUser.id).where(
                    AppUser.org_id == account.org_id, AppUser.crm_owner_id.is_not(None)
                )
            )
        }
        ids = [account_id, *(c.id for c in h.contacts), *(d.id for d in h.deals)]
        for row in session.execute(
            select(FieldHistory.entity_id, FieldHistory.field, FieldHistory.valid_from, FieldHistory.value)
            .where(FieldHistory.entity_id.in_(ids))
            .order_by(FieldHistory.valid_from)
        ):
            h.fields.add(row.entity_id, row.field, row.valid_from, row.value)
        return h

    # ------------------------------------------------------------ slicing

    def _exists(self, entity_id: uuid.UUID, fallback: datetime | None, t: datetime) -> bool:
        if self.fields.value(entity_id, "exists", t):
            return True
        if self.fields.since(entity_id, "exists", datetime.max.replace(tzinfo=UTC)) is not None:
            return False  # history says it was created later
        return fallback is not None and fallback <= t

    def as_of(self, t: datetime, *, lookback_days: int = 400) -> Snapshot | None:
        if t.tzinfo is None:
            raise ValueError("as_of needs a timezone-aware datetime")
        acc = self.account
        if not self._exists(acc.id, acc.created_in_source_at, t):
            return None
        f = self.fields

        deals = []
        for d in self.deals:
            if not self._exists(d.id, d.created_in_source_at, t):
                continue
            stage = f.value(d.id, "stage", t)
            deals.append(
                DealView(
                    id=d.id,
                    name=d.name,
                    deal_type=f.value(d.id, "deal_type", t, d.deal_type.value),
                    stage=stage,
                    is_closed=stage in self.won | self.lost,
                    is_won=stage in self.won,
                    amount=_dec(f.value(d.id, "amount", t)),
                    close_date=_day(f.value(d.id, "close_date", t)),
                    next_step=f.value(d.id, "next_step", t, "") or "",
                    next_step_since=f.since(d.id, "next_step", t),
                    next_step_ever_set=any(v for v in f.values_until(d.id, "next_step", t)),
                    next_step_date=_day(f.value(d.id, "next_step_date", t)),
                )
            )
        open_renewals = sorted(
            (d for d in deals if d.deal_type == "renewal" and not d.is_closed and d.close_date),
            key=lambda d: d.close_date or date.max,
        )
        renewal_deal = open_renewals[0] if open_renewals else None
        renewal_date = _day(f.value(acc.id, "renewal_date", t)) or (
            renewal_deal.close_date if renewal_deal else None
        )
        arr = _dec(f.value(acc.id, "arr", t))
        if arr is None and renewal_deal:
            arr = renewal_deal.amount
        if arr is None:
            won = sorted(
                (d for d in deals if d.is_won and d.close_date), key=lambda d: d.close_date or date.min
            )
            arr = won[-1].amount if won else None
        owner_crm = f.value(acc.id, "owner", t)
        account = AccountView(
            id=acc.id,
            name=f.value(acc.id, "name", t, acc.name),
            domains=tuple(f.value(acc.id, "domains", t, acc.domains) or ()),
            owner_user_id=self.owner_users.get(str(owner_crm)) if owner_crm else acc.owner_user_id,
            seats_purchased=f.value(acc.id, "seats_purchased", t),
            renewal_date=renewal_date,
            arr=arr,
            renewal_deal_id=renewal_deal.id if renewal_deal else None,
        )

        contacts = tuple(
            ContactView(
                id=c.id,
                email=c.email,
                name=c.name,
                title=f.value(c.id, "title", t, c.title),
                role=f.value(c.id, "role", t, "unknown"),
                status=f.value(c.id, "status", t, "active"),
                origin=c.origin,
                status_since=f.since(c.id, "status", t),
            )
            for c in self.contacts
            if self._exists(c.id, c.created_in_source_at, t)
        )

        earliest = t - timedelta(days=lookback_days)
        activities = tuple(
            ActivityView(
                id=a.id,
                type=a.type.value,
                direction=a.direction.value,
                source=a.source,
                occurred_at=a.occurred_at,
                known_at=a.known_at,
                cancelled=a.cancelled_at is not None and a.cancelled_at <= t,
                subject=a.subject,
                snippet=a.snippet,
                from_email=a.from_email,
                participant_emails=tuple(a.participant_emails or ()),
                contact_ids=tuple(a.contact_ids or ()),
                thread_id=a.thread_id,
                extra=dict(a.extra or {}),
                injection_flag=a.injection_flag,
                match_method=a.match_method.value,
                match_confidence=a.match_confidence,
                labels=tuple(self.labels.get(a.id, ())),
                injection_reason=a.injection_reason,
                injection_quote=a.injection_quote,
            )
            for a in self.activities
            if a.known_at <= t and a.occurred_at >= earliest
        )
        local_day = t.astimezone(self.tz).date()
        usage = tuple(
            UsageDay(u.date, u.active_users, u.key_actions, u.seats_used, dict(u.departments or {}))
            for u in self.usage
            if earliest.date() <= u.date < local_day
        )
        sources = {a.source for a in activities} | ({"usage"} if usage else set()) | {"crm"}
        return Snapshot(
            as_of=t,
            tz=self.tz,
            account=account,
            contacts=contacts,
            deals=tuple(sorted(deals, key=lambda d: d.close_date or date.max)),
            activities=activities,
            usage=usage,
            sources=frozenset(sources),
        )

    def field_changes(self, t: datetime) -> list[dict[str, Any]]:
        """Human-readable CRM field changes up to ``t`` for the timeline view."""
        names = {self.account.id: ("account", self.account.name)}
        names.update({c.id: ("contact", c.name or c.email) for c in self.contacts})
        names.update({d.id: ("deal", d.name) for d in self.deals})
        grouped: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
        for eid, name, at, value in self.fields.changes(t):
            if name == "exists" or eid not in names:
                continue
            kind, label = names[eid]
            grouped[at].append(
                {"entity": kind, "entity_id": str(eid), "label": label, "field": name, "value": value}
            )
        return [{"at": at, "changes": items} for at, items in sorted(grouped.items())]


def load_snapshot(session: Session, account_id: uuid.UUID, as_of: datetime) -> Snapshot | None:
    return AccountHistory.load(session, account_id).as_of(as_of)
