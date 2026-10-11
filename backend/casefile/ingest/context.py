from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from casefile.db.models import Account, AppUser, Contact, EntityType, FieldHistory, Org
from casefile.ingest.util import canonical_json, chunks


@dataclass
class NormalizeContext:
    """Per-sync state shared by the normalisers: lookups, caches and pending history rows."""

    session: Session
    org: Org
    mapping: dict[str, Any] = field(default_factory=dict)  # empty: this organisation's effective mapping
    reapply: bool = False  # re-normalising stored raw records: derived history is updated in place
    touched_accounts: set[uuid.UUID] = field(default_factory=set)
    _accounts: dict[tuple[str, str], uuid.UUID | None] = field(default_factory=dict)
    _owners: dict[str, uuid.UUID | None] = field(default_factory=dict)
    _contacts: dict[tuple[str, str], tuple[uuid.UUID, str] | None] = field(default_factory=dict)
    _history: list[dict[str, Any]] = field(default_factory=list)
    _own: set[str] | None = None

    def __post_init__(self) -> None:
        if not self.mapping:
            from casefile.ingest.mapping import org_mapping

            self.mapping = org_mapping(self.session, self.org.id)

    @property
    def org_id(self) -> uuid.UUID:
        return self.org.id

    @property
    def internal_domains(self) -> set[str]:
        return {d.lower() for d in self.org.internal_domains}

    @property
    def internal_addresses(self) -> set[str]:
        """Our own addresses (users, connected mailboxes), computed once per sync."""
        if self._own is None:
            from casefile.ingest.util import own_addresses

            self._own = own_addresses(self.session, self.org_id)
        return self._own

    def is_internal(self, address: str) -> bool:
        from casefile.ingest.util import is_ours

        return is_ours(address, self.internal_domains, self.internal_addresses)

    def now(self) -> datetime:
        return datetime.now(UTC)

    def account_id_for_crm(self, source: str, crm_id: str | None) -> uuid.UUID | None:
        if not crm_id:
            return None
        key = (source, crm_id)
        if key not in self._accounts or self._accounts[key] is None:
            self._accounts[key] = self.session.scalar(
                select(Account.id).where(
                    Account.org_id == self.org_id, Account.crm_source == source, Account.crm_id == crm_id
                )
            )
        return self._accounts[key]

    def contact_for_crm(self, source: str, crm_id: str) -> tuple[uuid.UUID, str] | None:
        """(contact id, email) for a CRM contact id, if we have that contact."""
        key = (source, crm_id)
        if self._contacts.get(key) is None:
            row = self.session.execute(
                select(Contact.id, Contact.email).where(
                    Contact.org_id == self.org_id, Contact.crm_source == source, Contact.crm_id == crm_id
                )
            ).first()
            self._contacts[key] = (row[0], row[1]) if row else None
        return self._contacts[key]

    def remember_account(self, source: str, crm_id: str, account_id: uuid.UUID) -> None:
        self._accounts[(source, crm_id)] = account_id

    def user_id_for_owner(self, crm_owner_id: str | None) -> uuid.UUID | None:
        if not crm_owner_id:
            return None
        if crm_owner_id not in self._owners or self._owners[crm_owner_id] is None:
            self._owners[crm_owner_id] = self.session.scalar(
                select(AppUser.id).where(AppUser.org_id == self.org_id, AppUser.crm_owner_id == crm_owner_id)
            )
        return self._owners[crm_owner_id]

    # ---------------------------------------------------------------- field history

    def history(
        self,
        entity_type: EntityType,
        entity_id: uuid.UUID,
        field_name: str,
        entries: list[tuple[datetime, Any]],
        source: str,
    ) -> None:
        """Queue history rows. Consecutive duplicates are collapsed; the DB ignores re-inserts."""
        last: str | None = None
        for at, value in sorted(entries, key=lambda e: e[0]):
            encoded = canonical_json(value)
            if encoded == last:
                continue
            last = encoded
            self._history.append(
                {
                    "org_id": self.org_id,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "field": field_name,
                    "value": value,
                    "valid_from": at,
                    "source": source,
                }
            )

    def flush(self) -> None:
        if not self._history:
            return
        for batch in chunks(self._history, 1000):
            stmt = insert(FieldHistory).values(batch)
            keys = ["org_id", "entity_type", "entity_id", "field", "valid_from"]
            if self.reapply:  # a new mapping can change what an old raw value means
                self.session.execute(
                    stmt.on_conflict_do_update(index_elements=keys, set_={"value": stmt.excluded.value})
                )
            else:
                self.session.execute(stmt.on_conflict_do_nothing(index_elements=keys))
        self._history.clear()
