"""Identity resolution: which customer account does each email, meeting and usage row belong to?

Ordered rules (plan section 6, Phase 1). Each match records its method and confidence:

    manual override ............ 1.0   (one record, an email address, or a whole domain)
    exact CRM contact email .... 1.0
    thread inheritance ......... 0.95  (a reply in a thread we already matched)
    account domain ............. 0.9   (free-mail domains and our own domains never count)
    meeting-attendee majority .. 0.8
    otherwise .................. ambiguous (several candidates) or unmatched

Unmatched and ambiguous records are never guessed into a score; they go to the blind-spot list.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from casefile.db.models import (
    Account,
    Activity,
    ActivityType,
    Contact,
    ContactRole,
    Deal,
    IdentityOverride,
    MatchMethod,
    Org,
    UsageDaily,
)
from casefile.ingest.util import FREEMAIL_DOMAINS, chunks, email_domain, is_system_address

UNRESOLVED = (MatchMethod.unmatched, MatchMethod.ambiguous)
SKIP_METHODS = (MatchMethod.crm_association, MatchMethod.internal_only)


@dataclass(frozen=True)
class Resolution:
    account_id: uuid.UUID | None
    method: MatchMethod
    confidence: float
    candidates: tuple[uuid.UUID, ...] = ()
    contact_ids: tuple[uuid.UUID, ...] = ()


@dataclass
class ResolveResult:
    activities_checked: int = 0
    activities_changed: int = 0
    usage_keys_matched: int = 0
    contacts_inferred: int = 0
    touched_accounts: set[uuid.UUID] = field(default_factory=set)


class IdentityIndex:
    def __init__(self, session: Session, org: Org) -> None:
        self.internal = {d.lower() for d in org.internal_domains}
        self.domain_accounts: dict[str, set[uuid.UUID]] = {}
        self.crm_accounts: dict[str, uuid.UUID] = {}
        for acc_id, domains, crm_id in session.execute(
            select(Account.id, Account.domains, Account.crm_id).where(Account.org_id == org.id)
        ):
            self.crm_accounts[str(crm_id)] = acc_id
            for d in domains or []:
                self.domain_accounts.setdefault(d.lower(), set()).add(acc_id)
        self.contacts: dict[str, tuple[uuid.UUID, uuid.UUID | None, str]] = {
            email: (cid, acc, origin)
            for cid, email, acc, origin in session.execute(
                select(Contact.id, Contact.email, Contact.account_id, Contact.origin).where(
                    Contact.org_id == org.id
                )
            )
        }
        self.overrides: dict[tuple[str, str], uuid.UUID] = {
            (kind, value.lower()): acc
            for kind, value, acc in session.execute(
                select(IdentityOverride.kind, IdentityOverride.value, IdentityOverride.account_id).where(
                    IdentityOverride.org_id == org.id
                )
            )
        }

    # ---------------------------------------------------------------- helpers

    def is_internal(self, email: str) -> bool:
        return email_domain(email) in self.internal

    def accounts_for_domain(self, domain: str) -> set[uuid.UUID]:
        """Exact domain, then parent domains (eu.globex.test → globex.test). Never free-mail."""
        domain = domain.lower()
        if (acc := self.overrides.get(("domain", domain))) is not None:
            return {acc}
        if domain in FREEMAIL_DOMAINS or domain in self.internal:
            return set()
        d = domain
        while d.count(".") >= 1:
            if d in self.domain_accounts:
                return set(self.domain_accounts[d])
            d = d.split(".", 1)[1]
        return set()

    def external(self, emails: list[str]) -> list[str]:
        return [e for e in emails if e and not self.is_internal(e) and not is_system_address(e)]

    # ---------------------------------------------------------------- rules

    def resolve(
        self, emails: list[str], *, kind: ActivityType, thread_account: uuid.UUID | None = None
    ) -> Resolution:
        external = self.external(emails)
        if not external:
            if thread_account:
                return Resolution(thread_account, MatchMethod.thread, 0.95)
            return Resolution(None, MatchMethod.internal_only, 0.0)

        known = [self.contacts[e] for e in external if e in self.contacts]
        contact_ids = tuple(c[0] for c in known)

        for e in external:
            if (acc := self.overrides.get(("email", e))) is not None:
                return Resolution(acc, MatchMethod.manual_override, 1.0, contact_ids=contact_ids)

        crm_accounts = {acc for _, acc, origin in known if acc and origin == "crm"}
        if len(crm_accounts) == 1:
            return Resolution(crm_accounts.pop(), MatchMethod.contact_email, 1.0, contact_ids=contact_ids)

        if thread_account and (not crm_accounts or thread_account in crm_accounts):
            return Resolution(thread_account, MatchMethod.thread, 0.95, contact_ids=contact_ids)

        per_email: list[set[uuid.UUID]] = []
        for e in external:
            if e in self.contacts and self.contacts[e][1] and self.contacts[e][2] == "crm":
                per_email.append({self.contacts[e][1]})  # type: ignore[arg-type]
            else:
                per_email.append(self.accounts_for_domain(email_domain(e)))
        candidates = set().union(*per_email) | crm_accounts

        if len(candidates) == 1:
            return Resolution(next(iter(candidates)), MatchMethod.domain, 0.9, contact_ids=contact_ids)

        if kind == ActivityType.meeting and len(candidates) > 1:
            votes = Counter(next(iter(s)) for s in per_email if len(s) == 1)
            if votes:
                top, count = votes.most_common(1)[0]
                if count / sum(votes.values()) > 0.5:
                    return Resolution(top, MatchMethod.meeting_majority, 0.8, contact_ids=contact_ids)

        if candidates:
            return Resolution(
                None, MatchMethod.ambiguous, 0.0, tuple(sorted(candidates, key=str)), contact_ids
            )
        return Resolution(None, MatchMethod.unmatched, 0.0, contact_ids=contact_ids)

    def resolve_usage_key(self, key: str) -> uuid.UUID | None:
        key = key.lower()
        if (acc := self.overrides.get(("usage_key", key))) is not None:
            return acc
        if key in self.crm_accounts:
            return self.crm_accounts[key]
        if "." in key:
            accounts = self.accounts_for_domain(key)
            if len(accounts) == 1:
                return next(iter(accounts))
        return None


# ---------------------------------------------------------------- passes


def relink_crm_refs(session: Session, org: Org) -> None:
    """Link contacts/deals whose CRM company arrived after them."""
    for model in (Contact, Deal):
        session.execute(
            update(model)
            .where(
                model.org_id == org.id,
                model.account_id.is_(None),
                model.crm_account_ref.is_not(None),
                Account.org_id == org.id,
                Account.crm_id == model.crm_account_ref,
            )
            .values(account_id=Account.id)
        )


def _infer_contacts(session: Session, org: Org, index: IdentityIndex, rows: Sequence[Any]) -> int:
    """People we only saw in email/calendar, at a domain that belongs to exactly one account."""
    first_seen: dict[str, tuple[uuid.UUID, datetime]] = {}
    for row in rows:
        for e in index.external(row.participant_emails or []):
            if e in index.contacts or email_domain(e) in FREEMAIL_DOMAINS:
                continue
            accounts = index.accounts_for_domain(email_domain(e))
            if len(accounts) != 1:
                continue
            acc = next(iter(accounts))
            if e not in first_seen or row.occurred_at < first_seen[e][1]:
                first_seen[e] = (acc, row.occurred_at)
    if not first_seen:
        return 0
    values = [
        {
            "org_id": org.id,
            "account_id": acc,
            "email": e,
            "name": e.split("@")[0].replace(".", " ").title(),
            "role": ContactRole.unknown,
            "origin": "inferred",
            "created_in_source_at": seen,
        }
        for e, (acc, seen) in first_seen.items()
    ]
    created = 0
    for batch in chunks(values, 500):
        result = session.execute(
            insert(Contact)
            .values(batch)
            .on_conflict_do_nothing(index_elements=["org_id", "email"])
            .returning(Contact.id, Contact.email, Contact.account_id)
        )
        for cid, email, linked in result:
            index.contacts[email] = (cid, linked, "inferred")
            created += 1
    return created


def resolve_identities(session: Session, org: Org, *, full: bool = False) -> ResolveResult:
    """Match activities and usage rows to accounts.

    ``full=False`` only looks at records not yet matched (the normal post-sync pass).
    ``full=True`` re-checks every non-CRM record, used after a manual override is added.
    """
    result = ResolveResult()
    index = IdentityIndex(session, org)

    cols = (
        Activity.id,
        Activity.type,
        Activity.thread_id,
        Activity.participant_emails,
        Activity.account_id,
        Activity.match_method,
        Activity.match_confidence,
        Activity.contact_ids,
        Activity.occurred_at,
    )
    query = select(*cols).where(Activity.org_id == org.id, Activity.match_method.not_in(SKIP_METHODS))
    if not full:
        query = query.where(Activity.match_method.in_(UNRESOLVED))
    rows = session.execute(query.order_by(Activity.occurred_at)).all()
    result.activities_checked = len(rows)
    result.contacts_inferred = _infer_contacts(session, org, index, rows)

    # Thread inheritance may only come from *other, earlier* messages. In the incremental pass the
    # rows being resolved are all unmatched, so every matched message in the DB is "other". In a
    # full pass every row is re-resolved, so the map is rebuilt from scratch in time order —
    # otherwise a message would inherit its own previous match.
    thread_accounts: dict[str, uuid.UUID] = (
        {}
        if full
        else {
            t: a
            for t, a in session.execute(
                select(Activity.thread_id, Activity.account_id).where(
                    Activity.org_id == org.id,
                    Activity.thread_id.is_not(None),
                    Activity.account_id.is_not(None),
                )
            )
            if t is not None and a is not None
        }
    )
    changes: list[dict[str, Any]] = []
    for row in rows:
        thread_acc = thread_accounts.get(row.thread_id) if row.thread_id else None
        res = index.resolve(list(row.participant_emails or []), kind=row.type, thread_account=thread_acc)
        if (pinned := index.overrides.get(("activity", str(row.id)))) is not None:
            res = Resolution(pinned, MatchMethod.manual_override, 1.0, contact_ids=res.contact_ids)
        if row.thread_id and res.account_id:
            thread_accounts.setdefault(row.thread_id, res.account_id)
        new = {
            "account_id": res.account_id,
            "match_method": res.method,
            "match_confidence": res.confidence,
            "match_candidates": [str(c) for c in res.candidates],
            "contact_ids": list(res.contact_ids),
        }
        old = {
            "account_id": row.account_id,
            "match_method": row.match_method,
            "match_confidence": row.match_confidence,
            "contact_ids": list(row.contact_ids or []),
        }
        if any(new[k] != old[k] for k in old):
            changes.append({"id": row.id, **new})
            result.touched_accounts.update(a for a in (row.account_id, res.account_id) if a)
    for batch in chunks(changes, 1000):
        session.execute(update(Activity), batch)
    result.activities_changed = len(changes)

    key_query = select(UsageDaily.external_account_key).where(UsageDaily.org_id == org.id)
    if not full:
        key_query = key_query.where(UsageDaily.account_id.is_(None))
    for key in session.scalars(key_query.distinct()):
        acc = index.resolve_usage_key(key)
        res_upd = session.execute(
            update(UsageDaily)
            .where(UsageDaily.org_id == org.id, UsageDaily.external_account_key == key)
            .where(UsageDaily.account_id.is_distinct_from(acc))
            .values(account_id=acc)
        )
        if acc and res_upd.rowcount:  # type: ignore[attr-defined]
            result.usage_keys_matched += 1
            result.touched_accounts.add(acc)
    return result
