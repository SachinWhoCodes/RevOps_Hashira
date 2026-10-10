from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from casefile.db.models import Account, AppUser, EntityType, FieldHistory, Org
from casefile.ingest.util import canonical_json, chunks, field_mapping


@dataclass
class NormalizeContext:
    """Per-sync state shared by the normalisers: lookups, caches and pending history rows."""

    session: Session
    org: Org
    mapping: dict[str, Any] = field(default_factory=field_mapping)
    touched_accounts: set[uuid.UUID] = field(default_factory=set)
    _accounts: dict[tuple[str, str], uuid.UUID | None] = field(default_factory=dict)
    _owners: dict[str, uuid.UUID | None] = field(default_factory=dict)
    _history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def org_id(self) -> uuid.UUID:
        return self.org.id

    @property
    def internal_domains(self) -> set[str]:
        return {d.lower() for d in self.org.internal_domains}

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
            self.session.execute(
                stmt.on_conflict_do_nothing(
                    index_elements=["org_id", "entity_type", "entity_id", "field", "valid_from"]
                )
            )
        self._history.clear()
