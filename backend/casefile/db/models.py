"""Schema v1 — the contract between tracks.

Conventions:
- Every table carries ``org_id`` (rule R7: multi-company from day one).
- All timestamps are timezone-aware UTC.
- ``field_history`` is the as-of backbone (rule R3): the snapshot loader reads entity
  field values from it, never from the "current value" columns, which exist for the UI.
- Activities carry ``known_at`` (when the source first knew about the record) separately
  from ``occurred_at`` (when it happens), so a meeting booked in March for May is visible
  as "booked" to an as-of-April snapshot but not to an as-of-February one.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _enum(e: type[enum.Enum]) -> Enum:
    return Enum(
        e,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


# Deterministic, table-scoped constraint names (Postgres needs them unique and stable for migrations).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        dict[str, Any]: JSONB,
        list[str]: ARRAY(Text),
        datetime: DateTime(timezone=True),
        uuid.UUID: UUID(as_uuid=True),
    }


# ---------------------------------------------------------------- enums


class UserRole(enum.StrEnum):
    owner = "owner"
    admin = "admin"
    leader = "leader"


class ConnectionKind(enum.StrEnum):
    fixture = "fixture"
    hubspot = "hubspot"
    gmail = "gmail"
    gcal = "gcal"
    usage_sql = "usage_sql"
    usage_csv = "usage_csv"
    slack = "slack"
    linear = "linear"


class Zone(enum.StrEnum):
    read = "read"
    write = "write"


class ConnectionStatus(enum.StrEnum):
    active = "active"
    error = "error"
    disabled = "disabled"


class SyncStatus(enum.StrEnum):
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class EntityType(enum.StrEnum):
    account = "account"
    contact = "contact"
    deal = "deal"


class ContactRole(enum.StrEnum):
    champion = "champion"
    economic_buyer = "economic_buyer"
    user = "user"
    unknown = "unknown"


class ContactStatus(enum.StrEnum):
    active = "active"
    left = "left"
    bounced = "bounced"


class DealType(enum.StrEnum):
    new = "new"
    renewal = "renewal"
    expansion = "expansion"
    other = "other"


class ActivityType(enum.StrEnum):
    email = "email"
    meeting = "meeting"
    call = "call"
    note = "note"
    task = "task"


class Direction(enum.StrEnum):
    inbound = "inbound"
    outbound = "outbound"
    internal = "internal"
    unknown = "unknown"


class MatchMethod(enum.StrEnum):
    crm_association = "crm_association"
    manual_override = "manual_override"
    contact_email = "contact_email"
    thread = "thread"
    domain = "domain"
    meeting_majority = "meeting_majority"
    ambiguous = "ambiguous"
    unmatched = "unmatched"
    internal_only = "internal_only"


class LabelStatus(enum.StrEnum):
    accepted = "accepted"
    possible = "possible"
    quarantined = "quarantined"


class Polarity(enum.StrEnum):
    risk = "risk"
    opportunity = "opportunity"
    urgency = "urgency"
    positive = "positive"


class AssessmentStatus(enum.StrEnum):
    pending = "pending"
    verified = "verified"
    fallback = "fallback"


class CaseKind(enum.StrEnum):
    risk = "risk"
    expansion = "expansion"


class CaseStatus(enum.StrEnum):
    open = "open"
    snoozed = "snoozed"
    resolved = "resolved"
    wrong = "wrong"


class ChangeSetStatus(enum.StrEnum):
    proposed = "proposed"
    partially_approved = "partially_approved"
    approved = "approved"
    executing = "executing"
    done = "done"
    failed = "failed"
    rejected = "rejected"
    superseded = "superseded"


class ChangeItemStatus(enum.StrEnum):
    proposed = "proposed"
    approved = "approved"
    edited_approved = "edited_approved"
    rejected = "rejected"
    queued = "queued"
    executing = "executing"
    succeeded = "succeeded"
    failed = "failed"
    conflict = "conflict"
    would_execute = "would_execute"
    blocked = "blocked"  # stopped by a rule (e.g. money talk in an AI draft); never executable
    superseded = "superseded"  # a newer proposal replaced it before anyone decided


class Decision(enum.StrEnum):
    approve = "approve"
    reject = "reject"


class ApprovalChannel(enum.StrEnum):
    web = "web"
    slack = "slack"


class RenewalOutcomeKind(enum.StrEnum):
    renewed = "renewed"
    downgraded = "downgraded"
    churned = "churned"
    late = "late"


class EvidenceLabel(enum.StrEnum):
    observed = "observed"
    simulated = "simulated"
    estimated = "estimated"
    untested = "untested"


class JobStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


# ---------------------------------------------------------------- helpers


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())


def _updated() -> Mapped[datetime]:
    return mapped_column(server_default=func.now(), onupdate=func.now())


def _org_fk() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("org.id", ondelete="CASCADE"), index=True)


# ---------------------------------------------------------------- identity


class Org(Base):
    __tablename__ = "org"

    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    # Our own company's email domains; never matched to a customer account.
    internal_domains: Mapped[list[str]] = mapped_column(default=list)
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = _created()


class AppUser(Base):
    __tablename__ = "app_user"
    __table_args__ = (UniqueConstraint("org_id", "email"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[UserRole] = mapped_column(_enum(UserRole), default=UserRole.owner)
    password_hash: Mapped[str | None] = mapped_column(Text)
    slack_user_id: Mapped[str | None] = mapped_column(String(64))
    crm_owner_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # Owners discovered from the CRM exist as rows but cannot log in until a password is set.
    is_active: Mapped[bool] = mapped_column(default=False)
    last_login_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()


# ---------------------------------------------------------------- connections and raw data


class Signup(Base):
    """A self-service registration waiting for its email to be confirmed. No organisation exists until
    the link is used, so an unconfirmed sign-up can never block the real owner of an email address.
    Not a tenant table (there is no tenant yet); only a hash of the token is stored."""

    __tablename__ = "signup"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    company: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    password_hash: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime]
    verified_at: Mapped[datetime | None]
    superseded_at: Mapped[datetime | None]
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("org.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()


class UserInvite(Base):
    """A one-time link that lets a person set their password. Only a hash of the token is stored;
    the link itself is shown once to the admin who created it."""

    __tablename__ = "user_invite"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()


class Connection(Base):
    __tablename__ = "connection"
    __table_args__ = (UniqueConstraint("org_id", "kind", "zone", "name"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    kind: Mapped[ConnectionKind] = mapped_column(_enum(ConnectionKind))
    zone: Mapped[Zone] = mapped_column(_enum(Zone), default=Zone.read)
    name: Mapped[str] = mapped_column(String(100), default="default")
    status: Mapped[ConnectionStatus] = mapped_column(_enum(ConnectionStatus), default=ConnectionStatus.active)
    # Non-secret settings only. A secret is either referenced by env-var name in ``secret_ref`` (set by
    # the operator) or pasted by an admin and stored encrypted in ``secret_enc`` (casefile.secretbox).
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    secret_ref: Mapped[str | None] = mapped_column(String(100))
    secret_enc: Mapped[str | None] = mapped_column(Text)
    # A write credential pasted by an admin, sealed with the executor's public key: the app can store it
    # but never read it back; only the executor process holds the private key (rule R1).
    secret_sealed: Mapped[str | None] = mapped_column(Text)
    cursor: Mapped[dict[str, Any]] = mapped_column(default=dict)
    interval_minutes: Mapped[int] = mapped_column(default=15)
    last_sync_started_at: Mapped[datetime | None]
    last_success_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None]
    consecutive_failures: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = _created()


class SyncRun(Base):
    __tablename__ = "sync_run"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = _org_fk()
    connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("connection.id", ondelete="CASCADE"))
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
    status: Mapped[SyncStatus] = mapped_column(_enum(SyncStatus), default=SyncStatus.running)
    pages: Mapped[int] = mapped_column(default=0)
    records_fetched: Mapped[int] = mapped_column(default=0)
    records_new: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)


class RawRecord(Base):
    """Every payload version we have fetched, kept for replay and audit."""

    __tablename__ = "raw_record"
    __table_args__ = (
        UniqueConstraint("org_id", "connection_id", "object_type", "external_id", "payload_hash"),
        Index("ix_raw_record_lookup", "org_id", "object_type", "external_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = _org_fk()
    connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("connection.id", ondelete="CASCADE"))
    object_type: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(256))
    payload: Mapped[dict[str, Any]]
    payload_hash: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(server_default=func.now())


# ---------------------------------------------------------------- entities


class Account(Base):
    __tablename__ = "account"
    __table_args__ = (
        UniqueConstraint("org_id", "crm_source", "crm_id"),
        Index("ix_account_domains", "domains", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    crm_source: Mapped[str] = mapped_column(String(32))
    crm_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(300))
    domains: Mapped[list[str]] = mapped_column(default=list)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    crm_owner_id: Mapped[str | None] = mapped_column(String(64))
    # Current-value cache for the UI. Analysis reads field_history via the snapshot loader.
    arr: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    renewal_date: Mapped[date | None] = mapped_column(Date)
    seats_purchased: Mapped[int | None]
    segment: Mapped[str | None] = mapped_column(String(64))
    created_in_source_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Contact(Base):
    __tablename__ = "contact"
    __table_args__ = (UniqueConstraint("org_id", "email"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="SET NULL"), index=True
    )
    crm_source: Mapped[str | None] = mapped_column(String(32))
    crm_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # The CRM's id for this person's company; lets us link once that company has synced.
    crm_account_ref: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str] = mapped_column(String(300), default="")
    title: Mapped[str | None] = mapped_column(String(300))
    role: Mapped[ContactRole] = mapped_column(_enum(ContactRole), default=ContactRole.unknown)
    status: Mapped[ContactStatus] = mapped_column(_enum(ContactStatus), default=ContactStatus.active)
    # "crm" when the CRM knows the person; "inferred" when we only saw them in email/calendar.
    origin: Mapped[str] = mapped_column(String(16), default="crm")
    created_in_source_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Deal(Base):
    __tablename__ = "deal"
    __table_args__ = (UniqueConstraint("org_id", "crm_source", "crm_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="SET NULL"), index=True
    )
    crm_source: Mapped[str] = mapped_column(String(32))
    crm_id: Mapped[str] = mapped_column(String(64))
    crm_account_ref: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(300), default="")
    deal_type: Mapped[DealType] = mapped_column(_enum(DealType), default=DealType.other)
    stage: Mapped[str | None] = mapped_column(String(64))
    is_closed: Mapped[bool] = mapped_column(default=False)
    is_won: Mapped[bool] = mapped_column(default=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    close_date: Mapped[date | None] = mapped_column(Date)
    next_step: Mapped[str | None] = mapped_column(Text)
    next_step_date: Mapped[date | None] = mapped_column(Date)
    crm_owner_id: Mapped[str | None] = mapped_column(String(64))
    created_in_source_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class FieldHistory(Base):
    """As-of backbone: one row per (entity, field, moment the value became true or known)."""

    __tablename__ = "field_history"
    __table_args__ = (
        UniqueConstraint("org_id", "entity_type", "entity_id", "field", "valid_from"),
        Index("ix_field_history_lookup", "entity_type", "entity_id", "field", "valid_from"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = _org_fk()
    entity_type: Mapped[EntityType] = mapped_column(_enum(EntityType))
    entity_id: Mapped[uuid.UUID]
    field: Mapped[str] = mapped_column(String(64))
    value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    valid_from: Mapped[datetime]
    source: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str | None] = mapped_column(String(256))


class Activity(Base):
    __tablename__ = "activity"
    __table_args__ = (
        UniqueConstraint("org_id", "source", "source_id"),
        Index("ix_activity_account_time", "org_id", "account_id", "occurred_at"),
        Index("ix_activity_thread", "org_id", "thread_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("account.id", ondelete="SET NULL"))
    type: Mapped[ActivityType] = mapped_column(_enum(ActivityType))
    direction: Mapped[Direction] = mapped_column(_enum(Direction), default=Direction.unknown)
    occurred_at: Mapped[datetime]
    known_at: Mapped[datetime]
    cancelled_at: Mapped[datetime | None]
    source: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(256))
    thread_id: Mapped[str | None] = mapped_column(String(256))
    dedupe_key: Mapped[str | None] = mapped_column(String(512))
    subject: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    # Full text is retention-limited; snippets are kept (see body_purge_after).
    body_text: Mapped[str | None] = mapped_column(Text)
    body_purge_after: Mapped[datetime | None]
    from_email: Mapped[str | None] = mapped_column(String(320))
    to_emails: Mapped[list[str]] = mapped_column(default=list)
    cc_emails: Mapped[list[str]] = mapped_column(default=list)
    participant_emails: Mapped[list[str]] = mapped_column(default=list)
    contact_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    crm_owner_id: Mapped[str | None] = mapped_column(String(64))
    extra: Mapped[dict[str, Any]] = mapped_column(default=dict)
    match_method: Mapped[MatchMethod] = mapped_column(_enum(MatchMethod), default=MatchMethod.unmatched)
    match_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    match_candidates: Mapped[list[str]] = mapped_column(default=list)
    injection_flag: Mapped[bool] = mapped_column(default=False)
    injection_reason: Mapped[str | None] = mapped_column(Text)
    injection_quote: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class UsageDaily(Base):
    """Aggregated product usage per customer per day. Counts only, no personal events."""

    __tablename__ = "usage_daily"
    __table_args__ = (
        UniqueConstraint("org_id", "external_account_key", "date"),
        Index("ix_usage_account_date", "org_id", "account_id", "date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = _org_fk()
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("account.id", ondelete="SET NULL"))
    # The product's own identifier for the customer (domain or CRM id), resolved to account_id.
    external_account_key: Mapped[str] = mapped_column(String(256))
    date: Mapped[date] = mapped_column(Date)
    active_users: Mapped[int] = mapped_column(default=0)
    key_actions: Mapped[int] = mapped_column(default=0)
    seats_used: Mapped[int | None]
    departments: Mapped[dict[str, Any]] = mapped_column(default=dict)
    source: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = _created()


class ExternalRef(Base):
    """Where one of our records lives in an outside system (e.g. the HubSpot sandbox company created
    for a synthetic account, or an owner's Slack user). Writers resolve targets through this; without a
    row, the record's own CRM id is used."""

    __tablename__ = "external_ref"
    __table_args__ = (UniqueConstraint("org_id", "entity_type", "entity_id", "system"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    entity_type: Mapped[str] = mapped_column(String(32))  # account | contact | deal | user
    entity_id: Mapped[uuid.UUID]
    system: Mapped[str] = mapped_column(String(32))  # hubspot | slack
    external_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = _created()


class IdentityOverride(Base):
    """A person's manual decision that an email, domain or usage key belongs to an account."""

    __tablename__ = "identity_override"
    __table_args__ = (UniqueConstraint("org_id", "kind", "value"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    kind: Mapped[str] = mapped_column(String(16))  # email | domain | usage_key
    value: Mapped[str] = mapped_column(String(320))
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created()


# ---------------------------------------------------------------- analysis (Phases 2-3)


class EmailLabel(Base):
    __tablename__ = "email_label"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    activity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("activity.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float)
    quote: Mapped[str | None] = mapped_column(Text)
    status: Mapped[LabelStatus] = mapped_column(_enum(LabelStatus))
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()


class Signal(Base):
    __tablename__ = "signal"
    __table_args__ = (Index("ix_signal_account_asof", "org_id", "account_id", "as_of"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assessment.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(64))
    polarity: Mapped[Polarity] = mapped_column(_enum(Polarity))
    value: Mapped[float] = mapped_column(Float)
    facts: Mapped[dict[str, Any]] = mapped_column(default=dict)
    window_start: Mapped[datetime | None]
    window_end: Mapped[datetime | None]
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    as_of: Mapped[datetime]
    engine_version: Mapped[str] = mapped_column(String(32))
    computed_at: Mapped[datetime] = _created()


class Assessment(Base):
    __tablename__ = "assessment"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[datetime]
    risk_score: Mapped[int] = mapped_column(default=0)
    opportunity_score: Mapped[int] = mapped_column(default=0)
    confidence: Mapped[str] = mapped_column(String(16), default="low")
    priority: Mapped[float] = mapped_column(Float, default=0.0)
    breakdown: Mapped[dict[str, Any]] = mapped_column(default=dict)
    headline: Mapped[str | None] = mapped_column(Text)
    claims: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    status: Mapped[AssessmentStatus] = mapped_column(
        _enum(AssessmentStatus), default=AssessmentStatus.pending
    )
    checker_report: Mapped[dict[str, Any]] = mapped_column(default=dict)
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    engine_version: Mapped[str] = mapped_column(String(32))
    # Changes only when the set of signals or their evidence changes (drives "re-assess on change").
    signature: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class AccountCase(Base):
    __tablename__ = "account_case"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    kind: Mapped[CaseKind] = mapped_column(_enum(CaseKind))
    status: Mapped[CaseStatus] = mapped_column(_enum(CaseStatus), default=CaseStatus.open)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    current_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assessment.id", ondelete="SET NULL")
    )
    opened_at: Mapped[datetime] = _created()
    resolved_at: Mapped[datetime | None]
    feedback: Mapped[str | None] = mapped_column(String(32))
    feedback_note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = _updated()


# ---------------------------------------------------------------- action (Phase 4)


class ChangeSet(Base):
    __tablename__ = "change_set"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_case.id", ondelete="CASCADE"), index=True)
    playbook_id: Mapped[str] = mapped_column(String(64))
    playbook_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[ChangeSetStatus] = mapped_column(_enum(ChangeSetStatus), default=ChangeSetStatus.proposed)
    # The assessment whose verified claims this plan was built from; a newer one supersedes the plan.
    assessment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assessment.id", ondelete="SET NULL"))
    # Planner notes: play purpose, skipped actions and why, how the draft was written, AI cost.
    notes: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class ChangeItem(Base):
    __tablename__ = "change_item"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    change_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("change_set.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int]
    action_type: Mapped[str] = mapped_column(String(64))
    target_system: Mapped[str] = mapped_column(String(32))
    target_ref: Mapped[dict[str, Any]] = mapped_column(default=dict)
    before: Mapped[dict[str, Any]] = mapped_column(default=dict)
    proposed_payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    final_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    tier: Mapped[int]
    evidence_ids: Mapped[list[str]] = mapped_column(default=list)
    why: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    status: Mapped[ChangeItemStatus] = mapped_column(
        _enum(ChangeItemStatus), default=ChangeItemStatus.proposed
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    external_result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Approval(Base):
    __tablename__ = "approval"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    change_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("change_item.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    decision: Mapped[Decision] = mapped_column(_enum(Decision))
    edited: Mapped[bool] = mapped_column(default=False)
    approved_payload_hash: Mapped[str | None] = mapped_column(String(64))
    channel: Mapped[ApprovalChannel] = mapped_column(_enum(ApprovalChannel), default=ApprovalChannel.web)
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = _created()


class ExecutionAttempt(Base):
    __tablename__ = "execution_attempt"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = _org_fk()
    change_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("change_item.id", ondelete="CASCADE"), index=True
    )
    attempt_no: Mapped[int]
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None]
    error: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(256))


# ---------------------------------------------------------------- trust


class AuditLog(Base):
    """Append-only (DB trigger blocks UPDATE/DELETE) and hash-chained (rule R5)."""

    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = _org_fk()
    ts: Mapped[datetime] = mapped_column(server_default=func.now())
    actor_type: Mapped[str] = mapped_column(String(16))  # user | system | agent
    actor_id: Mapped[str | None] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(128))
    evidence_ids: Mapped[list[str]] = mapped_column(default=list)
    approval_id: Mapped[uuid.UUID | None]
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class LlmCall(Base):
    __tablename__ = "llm_call"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = _org_fk()
    purpose: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    cache_read_tokens: Mapped[int] = mapped_column(default=0)
    cache_write_tokens: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    latency_ms: Mapped[int] = mapped_column(default=0)
    stop_reason: Mapped[str | None] = mapped_column(String(32))
    ok: Mapped[bool] = mapped_column(default=True)
    error: Mapped[str | None] = mapped_column(Text)
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("account.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()


class LlmCache(Base):
    """Validated AI outputs keyed by a hash of (prompt version, model, exact input).
    The same input is never paid for twice; a prompt or model change makes a new key."""

    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[uuid.UUID] = _org_fk()
    purpose: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(64))
    output: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = _created()


# ---------------------------------------------------------------- proof (Phase 5)


class RenewalOutcome(Base):
    __tablename__ = "renewal_outcome"
    __table_args__ = (UniqueConstraint("org_id", "account_id", "renewal_date"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    deal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deal.id", ondelete="SET NULL"))
    renewal_date: Mapped[date] = mapped_column(Date)
    outcome: Mapped[RenewalOutcomeKind] = mapped_column(_enum(RenewalOutcomeKind))
    arr_before: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    arr_after: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    label_source: Mapped[str] = mapped_column(String(16), default="derived")
    decided_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()


class BacktestRun(Base):
    __tablename__ = "backtest_run"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="running")
    label: Mapped[EvidenceLabel] = mapped_column(_enum(EvidenceLabel))
    engine_version: Mapped[str] = mapped_column(String(32))
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    started_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None]


class BacktestResult(Base):
    __tablename__ = "backtest_result"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("backtest_run.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    renewal_date: Mapped[date | None] = mapped_column(Date)
    horizon_days: Mapped[int | None]
    as_of: Mapped[datetime]
    score: Mapped[float] = mapped_column(Float)
    rank: Mapped[int | None]
    flagged: Mapped[bool] = mapped_column(default=False)
    outcome: Mapped[str | None] = mapped_column(String(32))
    baseline_scores: Mapped[dict[str, Any]] = mapped_column(default=dict)


class EvalRun(Base):
    __tablename__ = "eval_run"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    suite: Mapped[str] = mapped_column(String(64))
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    label: Mapped[EvidenceLabel] = mapped_column(_enum(EvidenceLabel))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()


# ---------------------------------------------------------------- configuration


class Policy(Base):
    __tablename__ = "policy"
    __table_args__ = (UniqueConstraint("org_id", "action_type"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    action_type: Mapped[str] = mapped_column(String(64))
    tier: Mapped[int]
    enabled: Mapped[bool] = mapped_column(default=True)


class ConfigVersion(Base):
    __tablename__ = "config_version"
    __table_args__ = (UniqueConstraint("org_id", "kind", "version"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    kind: Mapped[str] = mapped_column(String(32))  # thresholds | scoring | playbooks | field_mapping
    version: Mapped[int]
    body: Mapped[dict[str, Any]]
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created()


class PlayOutcome(Base):
    """Level 2 outcome tracker: did the signals improve after an executed change set?"""

    __tablename__ = "play_outcome"
    __table_args__ = (UniqueConstraint("change_set_id", "check_day"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    change_set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("change_set.id", ondelete="CASCADE"))
    check_day: Mapped[int]
    measured_at: Mapped[datetime | None]
    signals_before: Mapped[dict[str, Any]] = mapped_column(default=dict)
    signals_after: Mapped[dict[str, Any]] = mapped_column(default=dict)
    result: Mapped[str] = mapped_column(String(16), default="pending")


# ---------------------------------------------------------------- jobs


class Job(Base):
    """Postgres-backed work queue, consumed with SELECT ... FOR UPDATE SKIP LOCKED."""

    __tablename__ = "job"
    __table_args__ = (
        Index(
            "uq_job_queued_dedupe",
            "kind",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status = 'queued' AND dedupe_key IS NOT NULL"),
        ),
        Index("ix_job_ready", "status", "run_after"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("org.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus), default=JobStatus.queued)
    run_after: Mapped[datetime] = mapped_column(server_default=func.now())
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=5)
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None]
    locked_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None]
