"""Onboarding a company (C4): connect → backfill → check → back-test report → shadow → live.

Each organisation is in one of three modes (``org.settings["mode"]``):

- ``setup``  — data is connected and back-filled and accounts are scored, but no AI runs (no email
  reading, explanations or plans: nothing to pay for until the mapping is right) and nothing is written.
- ``shadow`` — the full loop runs and people approve lines, but the executor records ``would_execute``
  instead of writing (the pilot's "two to three weeks of shadow mode").
- ``live``   — approved lines are written. An admin can drop back to shadow at any time (the off switch).

An organisation without a mode is treated as ``setup`` (fail safe). Organisations that existed before
onboarding were marked ``live`` by migration ``c4a8e2f1d903``.

Step states are derived from the data each time, never stored, so they cannot drift from reality.
Only decisions are stored: the data-permission record, mode changes (audited), a skipped usage step.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.audit.log import append as audit
from casefile.config import get_settings
from casefile.connectors.base import AuthError, ConnectorError
from casefile.connectors.hubspot.live import HubSpotLive
from casefile.db.models import (
    Account,
    AccountCase,
    Activity,
    AppUser,
    BacktestRun,
    ChangeItem,
    ChangeItemStatus,
    ChangeSet,
    Connection,
    ConnectionKind,
    ConnectionStatus,
    Contact,
    ContactRole,
    Deal,
    DealType,
    EvidenceLabel,
    Org,
    RawRecord,
    SyncRun,
    UsageDaily,
    Zone,
)
from casefile.db.tenant import system_scope
from casefile.worker.jobs import enqueue

MODES = ("setup", "shadow", "live")
TRANSITIONS = {("setup", "shadow"), ("shadow", "live"), ("live", "shadow"), ("shadow", "setup")}
SHADOW_MIN_DAYS = 14
SHADOW_MIN_DECISIONS = 5
HEARTBEAT_FRESH = timedelta(minutes=10)
USAGE_MAX_BYTES = 50 * 1024 * 1024
USAGE_REQUIRED = ("account_key", "date", "active_users", "key_actions")
DOMAIN = re.compile(r"^(?=.{3,253}$)[a-z0-9-]+(\.[a-z0-9-]+)+$")
# What the HubSpot reader needs. Emails and meetings are optional: without them Casefile still works
# from the CRM, with less evidence (the reader skips them on 403 as well).
HUBSPOT_REQUIRED = ("companies", "contacts", "deals", "owners")
HUBSPOT_OPTIONAL = {"emails", "meetings"}

PERMISSION_VERSION = "2026-10-11"


def permission_statements(org: Org) -> list[str]:
    return [
        f"I am authorised by {org.name} to connect its CRM and product-usage data to Casefile, which "
        "reads customer companies, contacts, deals, and the emails and meetings logged there.",
        "Customer text is sent to Anthropic's API to read emails and write explanations, with email "
        "addresses and long numbers (phone, card, ID) masked first. Nothing is sent while in setup.",
        "Full email text is deleted after 90 days; short snippets, AI labels and the audit log are kept "
        "as evidence.",
        "Casefile writes nothing to our tools until an admin switches to live, and then only lines a "
        "person approved. Emails to customers are only ever saved as drafts, never sent.",
    ]


class OnboardingError(ValueError):
    """A request that cannot be carried out; the message is shown to the admin as is."""


# ---------------------------------------------------------------- modes


def org_mode(org: Org) -> str:
    mode = (org.settings or {}).get("mode")
    return mode if mode in MODES else "setup"


def _set_settings(org: Org, **changes: Any) -> None:
    org.settings = {**(org.settings or {}), **changes}  # a new dict: in-place JSONB edits are not saved


def _change_mode(
    session: Session, org: Org, user: AppUser, new: str, reason: str | None, **details: Any
) -> None:
    old = org_mode(org)
    if (old, new) not in TRANSITIONS:
        raise OnboardingError(f"cannot switch from {old} to {new}")
    now = datetime.now(UTC)
    history = [*(org.settings or {}).get("mode_history", []), {"mode": new, "at": now.isoformat()}]
    _set_settings(org, mode=new, mode_since=now.isoformat(), mode_history=history[-20:])
    audit(
        session,
        org.id,
        "org_mode_changed",
        actor_type="user",
        actor_id=str(user.id),
        entity_type="org",
        entity_id=str(org.id),
        details={"from": old, "to": new, "reason": reason, **details},
    )


def _since(org: Org) -> datetime | None:
    raw = (org.settings or {}).get("mode_since")
    return datetime.fromisoformat(raw) if raw else None


def _shadow_started(org: Org) -> datetime | None:
    """When the latest stretch of shadow mode began (also while live: the shadow period before it)."""
    started = None
    for entry in (org.settings or {}).get("mode_history", []):
        if entry["mode"] == "shadow" and started is None:
            started = datetime.fromisoformat(entry["at"])
        elif entry["mode"] == "setup":
            started = None
    return started


# ---------------------------------------------------------------- company and permission


def update_company(
    session: Session, org: Org, user: AppUser, *, name: str, timezone: str, internal_domains: list[str]
) -> bool:
    name = name.strip()
    if not 1 <= len(name) <= 200:
        raise OnboardingError("company name is required")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise OnboardingError(f"unknown time zone {timezone!r}") from exc
    domains = sorted({d.strip().lower().removeprefix("@") for d in internal_domains if d.strip()})
    bad = [d for d in domains if not DOMAIN.match(d)]
    if bad or not domains:
        raise OnboardingError("give at least one email domain your staff use, e.g. yourcompany.com")
    relink = domains != sorted(org.internal_domains or [])
    changed = {k for k, a, b in (("name", name, org.name), ("timezone", timezone, org.timezone)) if a != b}
    if relink:
        changed.add("internal_domains")
    if not changed:
        return False
    org.name, org.timezone, org.internal_domains = name, timezone, domains
    audit(
        session,
        org.id,
        "org_updated",
        actor_type="user",
        actor_id=str(user.id),
        entity_type="org",
        entity_id=str(org.id),
        details={"changed": sorted(changed), "internal_domains": domains},
    )
    if relink and session.scalar(
        select(func.count()).select_from(RawRecord).where(RawRecord.org_id == org.id)
    ):
        # Who counts as "us" changed: re-match people and rescore from the stored records.
        enqueue(
            session,
            "reapply_mapping",
            {"user_id": str(user.id)},
            org_id=org.id,
            dedupe_key=f"reapply:{org.id}",
        )
    return True


def record_permission(session: Session, org: Org, user: AppUser, accepted: list[str]) -> dict[str, Any]:
    statements = permission_statements(org)
    if accepted != statements:
        raise OnboardingError(
            "every statement must be confirmed as shown (the text may have changed; reload)"
        )
    digest = hashlib.sha256("\n".join(statements).encode()).hexdigest()
    record = {
        "version": PERMISSION_VERSION,
        "by": str(user.id),
        "by_email": user.email,
        "at": datetime.now(UTC).isoformat(),
        "text_sha256": digest,
        "statements": statements,
    }
    _set_settings(org, data_permission=record)
    audit(
        session,
        org.id,
        "data_permission_recorded",
        actor_type="user",
        actor_id=str(user.id),
        entity_type="org",
        entity_id=str(org.id),
        details={"version": PERMISSION_VERSION, "text_sha256": digest, "statements": statements},
    )
    return record


def _require_permission(org: Org) -> None:
    if not (org.settings or {}).get("data_permission"):
        raise OnboardingError("record the data permission first")


# ---------------------------------------------------------------- HubSpot


def _portal_in_use(session: Session, org: Org, portal_id: str) -> str | None:
    """Why this portal cannot be this organisation's CRM, or None. Looks across organisations (one
    explicit row-level-security bypass), because the same CRM must never feed two companies."""
    system_scope(session)
    try:
        conns = session.scalars(select(Connection).where(Connection.kind == ConnectionKind.hubspot)).all()
        for conn in conns:
            cfg = conn.config or {}
            if str(cfg.get("portal_id")) != portal_id:
                continue
            if conn.org_id != org.id:
                return "this HubSpot portal is already connected to another Casefile organisation"
            if conn.zone == Zone.write or cfg.get("mode") == "sandbox_mirror":
                return (
                    "this is the sandbox portal Casefile writes test changes into; reading it as your CRM "
                    "would pull in everything else stored there"
                )
    finally:
        system_scope(session, False)
    return None


def connect_hubspot(
    session: Session, org: Org, user: AppUser, token: str, *, live: HubSpotLive | None = None
) -> dict[str, Any]:
    """Check a read-only HubSpot token and store it encrypted. Nothing is synced until this succeeds."""
    from casefile import secretbox
    from casefile.ingest.mapping import org_mapping

    _require_permission(org)
    token = token.strip()
    if not 10 <= len(token) <= 500 or any(ch.isspace() for ch in token):
        raise OnboardingError("that does not look like a HubSpot access token")
    if not secretbox.available():
        raise OnboardingError(
            "the server cannot store credentials yet (SECRETS_KEY is not set); ask your operator"
        )
    live = live or HubSpotLive(token)
    try:
        account = live.account()
    except AuthError as exc:
        raise OnboardingError(
            "HubSpot did not accept this token (revoked, mistyped, or from another app)"
        ) from exc
    except ConnectorError as exc:
        raise OnboardingError(f"could not reach HubSpot: {exc}") from exc
    portal = str(account["portal_id"])
    if why := _portal_in_use(session, org, portal):
        raise OnboardingError(why)
    existing = session.scalar(
        select(Connection).where(
            Connection.org_id == org.id,
            Connection.kind == ConnectionKind.hubspot,
            Connection.zone == Zone.read,
            Connection.name == "crm",
        )
    )
    if existing is not None and str((existing.config or {}).get("portal_id")) != portal:
        raise OnboardingError(
            f"this organisation is connected to HubSpot portal {existing.config.get('portal_id')}; "
            "a token for a different portal would mix two CRMs"
        )
    engagements = list(org_mapping(session, org.id)["hubspot"].get("engagement_types", []))
    access = live.can_read([*HUBSPOT_REQUIRED, *engagements])
    required = {*HUBSPOT_REQUIRED, *(e for e in engagements if e not in HUBSPOT_OPTIONAL)}
    missing = sorted(name for name in required if access.get(name) != "ok")
    if missing:
        raise OnboardingError(
            "the token cannot read: "
            + ", ".join(missing)
            + ". Add those read scopes to the app and try again."
        )
    if any(v == "ok" for v in live.can_write().values()):
        raise OnboardingError(
            "this token can also change records. The read connection must be read-only: create a "
            "separate app with read scopes only (write access is connected separately)"
        )
    warnings = [
        f"cannot read {name}: Casefile will work without them, with less evidence"
        for name in sorted(HUBSPOT_OPTIONAL & set(engagements))
        if access.get(name) != "ok"
    ]
    config = {
        "portal_id": portal,
        "time_zone": account.get("time_zone"),
        "currency": account.get("currency"),
        "ui_domain": account.get("ui_domain"),
        "access": access,
        "connected_by": user.email,
    }
    if existing is None:
        conn = Connection(
            org_id=org.id,
            kind=ConnectionKind.hubspot,
            zone=Zone.read,
            name="crm",
            interval_minutes=get_settings().crm_sync_interval_minutes,
            cursor={},
        )
        session.add(conn)
        action = "connection_added"
    else:
        conn = existing
        action = "connection_credential_replaced"
    conn.config, conn.secret_enc, conn.secret_ref = config, secretbox.seal(token), None
    conn.status, conn.consecutive_failures, conn.last_error = ConnectionStatus.active, 0, None
    session.flush()
    audit(
        session,
        org.id,
        action,
        actor_type="user",
        actor_id=str(user.id),
        entity_type="connection",
        entity_id=str(conn.id),
        details={"kind": "hubspot", "portal_id": portal, "access": access},  # never the token
    )
    enqueue(
        session,
        "sync_connection",
        {"connection_id": str(conn.id)},
        org_id=org.id,
        dedupe_key=f"sync:{conn.id}",
    )
    return {"connection_id": str(conn.id), "portal_id": portal, "access": access, "warnings": warnings}


# ---------------------------------------------------------------- product usage (CSV upload)


def _int_cell(value: str | None) -> int:
    n = int(float(value or ""))
    if n < 0:
        raise ValueError
    return n


def parse_usage_csv(content: bytes) -> dict[str, Any]:
    """Validate an upload without storing it: rows, date range, the first few problems with line numbers."""
    if len(content) > USAGE_MAX_BYTES:
        raise OnboardingError("the file is larger than 50 MB; send fewer days or aggregate per day")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise OnboardingError("the file is not UTF-8 text") from exc
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in reader.fieldnames or []]
    missing = [c for c in USAGE_REQUIRED if c not in header]
    if missing:
        raise OnboardingError("missing columns: " + ", ".join(missing) + " (see the template)")
    problems: list[str] = []
    keys: set[str] = set()
    rows = 0
    first: date | None = None
    last: date | None = None
    for line, row in enumerate(reader, start=2):
        row = {(k or "").strip(): v for k, v in row.items()}
        try:
            day = date.fromisoformat(str(row["date"]).strip()[:10])
            _int_cell(row["active_users"])
            _int_cell(row["key_actions"])
            if (row.get("seats_used") or "").strip():
                _int_cell(row["seats_used"])
            key = str(row["account_key"] or "").strip().lower()
            if not key:
                raise ValueError
        except (ValueError, TypeError):
            if len(problems) < 5:
                problems.append(f"line {line}: needs account_key, a YYYY-MM-DD date and whole numbers ≥ 0")
            continue
        rows += 1
        keys.add(key)
        first = day if first is None or day < first else first
        last = day if last is None or day > last else last
    if problems:
        raise OnboardingError("; ".join(problems))
    if rows == 0:
        raise OnboardingError("the file has a header but no rows")
    return {
        "rows": rows,
        "keys": sorted(keys),
        "first_day": first.isoformat() if first else None,
        "last_day": last.isoformat() if last else None,
    }


def upload_usage(session: Session, org: Org, user: AppUser, filename: str, content: bytes) -> dict[str, Any]:
    from casefile.identity.resolver import IdentityIndex

    _require_permission(org)
    parsed = parse_usage_csv(content)
    index = IdentityIndex(session, org)
    matched = sum(index.resolve_usage_key(k) is not None for k in parsed["keys"])
    folder = get_settings().upload_dir / str(org.id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "usage.csv"
    tmp = folder / f".usage-{uuid.uuid4().hex}.tmp"
    tmp.write_bytes(content)
    os.replace(tmp, path)  # atomic: the worker never reads a half-written file
    conn = session.scalar(
        select(Connection).where(
            Connection.org_id == org.id,
            Connection.kind == ConnectionKind.usage_csv,
            Connection.zone == Zone.read,
            Connection.name == "upload",
        )
    )
    if conn is None:
        conn = Connection(
            org_id=org.id, kind=ConnectionKind.usage_csv, zone=Zone.read, name="upload", interval_minutes=60
        )
        session.add(conn)
    summary = {k: v for k, v in parsed.items() if k != "keys"} | {
        "accounts_in_file": len(parsed["keys"]),
        "matched_now": matched,
    }
    conn.config = {"path": str(Path(path).resolve()), "filename": Path(filename).name[:200], **summary}
    conn.cursor = {}  # read the whole new file
    conn.status = ConnectionStatus.active
    _set_settings(org, usage_skipped=False)
    session.flush()
    audit(
        session,
        org.id,
        "usage_uploaded",
        actor_type="user",
        actor_id=str(user.id),
        entity_type="connection",
        entity_id=str(conn.id),
        details=summary,
    )
    enqueue(
        session,
        "sync_connection",
        {"connection_id": str(conn.id)},
        org_id=org.id,
        dedupe_key=f"sync:{conn.id}",
    )
    return summary


def skip_usage(session: Session, org: Org, user: AppUser) -> None:
    _set_settings(org, usage_skipped=True)
    audit(
        session,
        org.id,
        "usage_skipped",
        actor_type="user",
        actor_id=str(user.id),
        entity_type="org",
        entity_id=str(org.id),
    )


# ---------------------------------------------------------------- the checklist


def data_label(session: Session, org: Org) -> EvidenceLabel:
    """Synthetic data (a fixture connection) gives simulated results; a real CRM gives observed ones."""
    fixture = session.scalar(
        select(func.count())
        .select_from(Connection)
        .where(Connection.org_id == org.id, Connection.kind == ConnectionKind.fixture)
    )
    return EvidenceLabel.simulated if fixture else EvidenceLabel.observed


def _conn_view(session: Session, conn: Connection) -> dict[str, Any]:
    last = session.scalar(
        select(SyncRun).where(SyncRun.connection_id == conn.id).order_by(SyncRun.id.desc()).limit(1)
    )
    cfg = {k: v for k, v in (conn.config or {}).items() if k not in ("path",)}
    return {
        "id": str(conn.id),
        "kind": conn.kind.value,
        "name": conn.name,
        "status": conn.status.value,
        "config": cfg,
        "last_success_at": conn.last_success_at.isoformat() if conn.last_success_at else None,
        "last_error": conn.last_error,
        "last_run": None
        if last is None
        else {
            "status": last.status.value,
            "started_at": last.started_at.isoformat(),
            "finished_at": last.finished_at.isoformat() if last.finished_at else None,
            "fetched": last.records_fetched,
        },
    }


def _sources(session: Session, org: Org) -> list[Connection]:
    conns = session.scalars(
        select(Connection).where(
            Connection.org_id == org.id,
            Connection.zone == Zone.read,
            Connection.kind.in_([ConnectionKind.hubspot, ConnectionKind.fixture]),
        )
    ).all()
    return [c for c in conns if (c.config or {}).get("mode") != "sandbox_mirror"]


def _data_counts(session: Session, org: Org) -> dict[str, Any]:
    def count(model: Any, *where: Any) -> int:
        return int(
            session.scalar(select(func.count()).select_from(model).where(model.org_id == org.id, *where)) or 0
        )

    accounts = count(Account)
    with_champion = int(
        session.scalar(
            select(func.count(func.distinct(Contact.account_id))).where(
                Contact.org_id == org.id,
                Contact.role == ContactRole.champion,
                Contact.account_id.is_not(None),
            )
        )
        or 0
    )
    activities = count(Activity)
    return {
        "accounts": accounts,
        "accounts_with_renewal_date": count(Account, Account.renewal_date.is_not(None)),
        "accounts_with_owner": count(Account, Account.owner_user_id.is_not(None)),
        "accounts_with_recorded_champion": with_champion,
        "contacts": count(Contact),
        "deals": count(Deal),
        "renewal_deals": count(Deal, Deal.deal_type == DealType.renewal),
        "activities": activities,
        "activities_unmatched": count(Activity, Activity.account_id.is_(None)),
        "usage_days": count(UsageDaily),
        "usage_days_unmatched": count(UsageDaily, UsageDaily.account_id.is_(None)),
    }


def _backtest_view(run: BacktestRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    m = run.metrics or {}
    h30 = ((m.get("horizon") or {}).get("30") or {}).get("all") or {}
    held = (((m.get("horizon") or {}).get("30") or {}).get("held_out")) or {}
    positives = int(h30.get("positives") or 0)
    return {
        "id": str(run.id),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "label": run.label.value,
        "events": m.get("events"),
        "positives_30d": positives,
        "auc_30d": h30.get("auc"),
        "held_out_auc_30d": held.get("auc"),
        "recall_30d": h30.get("recall"),
        "best_single_signal": (h30.get("best_single_signal") or {}).get("signal"),
        "best_single_signal_auc": ((h30.get("best_single_signal") or {}).get("auc")),
        # The back-test's own rule: a headline needs 10+ lost renewals in the held-out half.
        "enough_history": bool(m.get("headline_ok")),
        "note": m.get("fallback"),
    }


def _shadow_stats(session: Session, org: Org) -> dict[str, Any] | None:
    started = _shadow_started(org)
    if started is None:
        return None
    ended = _since(org) if org_mode(org) == "live" else None
    window = [ChangeSet.org_id == org.id, ChangeSet.created_at >= started]
    if ended is not None:
        window.append(ChangeSet.created_at < ended)
    statuses = dict(
        session.execute(
            select(ChangeItem.status, func.count())
            .join(ChangeSet, ChangeSet.id == ChangeItem.change_set_id)
            .where(*window)
            .group_by(ChangeItem.status)
        ).all()
    )
    s = ChangeItemStatus
    decided = sum(n for k, n in statuses.items() if k not in (s.proposed, s.superseded, s.blocked))
    cases = int(
        session.scalar(
            select(func.count())
            .select_from(AccountCase)
            .where(AccountCase.org_id == org.id, AccountCase.opened_at >= started)
        )
        or 0
    )
    end = ended or datetime.now(UTC)
    return {
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat() if ended else None,
        "days": (end - started).days,
        "cases_opened": cases,
        "change_sets": int(session.scalar(select(func.count()).select_from(ChangeSet).where(*window)) or 0),
        "lines_decided": decided,
        "lines_rejected": statuses.get(s.rejected, 0),
        "would_execute": statuses.get(s.would_execute, 0),
    }


def _writers(session: Session, org: Org, now: datetime) -> list[dict[str, Any]]:
    out = []
    for conn in session.scalars(
        select(Connection).where(Connection.org_id == org.id, Connection.zone == Zone.write)
    ):
        beat = (conn.cursor or {}).get("heartbeat")
        fresh = bool(beat and now - datetime.fromisoformat(beat) <= HEARTBEAT_FRESH)
        out.append(
            {
                "kind": conn.kind.value,
                "name": conn.name,
                "ready": fresh and bool((conn.cursor or {}).get("writer")),
            }
        )
    return out


def live_checks(session: Session, org: Org, now: datetime | None = None) -> list[dict[str, Any]]:
    """What going live needs. ``required`` checks block; the others can be overridden with a reason."""
    now = now or datetime.now(UTC)
    stats = _shadow_stats(session, org) or {}
    writers = _writers(session, org, now)
    return [
        {
            "key": "in_shadow",
            "required": True,
            "ok": org_mode(org) == "shadow",
            "text": "Shadow mode is running",
        },
        {
            "key": "writer_ready",
            "required": True,
            "ok": any(w["ready"] for w in writers),
            "text": "The executor is running with at least one write tool (seen in the last 10 minutes)",
        },
        {
            "key": "shadow_days",
            "required": False,
            "ok": stats.get("days", 0) >= SHADOW_MIN_DAYS,
            "text": f"At least {SHADOW_MIN_DAYS} days in shadow mode (so far {stats.get('days', 0)})",
        },
        {
            "key": "decisions",
            "required": False,
            "ok": stats.get("lines_decided", 0) >= SHADOW_MIN_DECISIONS,
            "text": f"People decided at least {SHADOW_MIN_DECISIONS} proposed lines in shadow mode "
            f"(so far {stats.get('lines_decided', 0)})",
        },
    ]


def status(session: Session, org: Org) -> dict[str, Any]:
    now = datetime.now(UTC)
    settings = org.settings or {}
    mode = org_mode(org)
    sources = _sources(session, org)
    usage = session.scalars(
        select(Connection).where(
            Connection.org_id == org.id,
            Connection.zone == Zone.read,
            Connection.kind.in_([ConnectionKind.usage_csv, ConnectionKind.usage_sql]),
        )
    ).all()
    counts = _data_counts(session, org)
    run = session.scalar(
        select(BacktestRun)
        .where(BacktestRun.org_id == org.id, BacktestRun.status == "done")
        .order_by(BacktestRun.started_at.desc())
        .limit(1)
    )
    backtest = _backtest_view(run)
    synced = any(c.last_success_at for c in sources)
    permission = settings.get("data_permission")

    def state(done: bool, *, warn: bool = False, optional: bool = False) -> str:
        if done:
            return "warn" if warn else "done"
        return "optional" if optional else "todo"

    data_warnings = []
    if synced and counts["accounts"] == 0:
        data_warnings.append(
            "No customer companies were found. Check the company properties in the field mapping."
        )
    if synced and counts["accounts"] and counts["renewal_deals"] == 0:
        data_warnings.append(
            "No renewal deals were found, so the back-test has nothing to learn from. Check the deal type "
            "values in the field mapping."
        )
    if counts["accounts"] and counts["accounts_with_renewal_date"] < counts["accounts"] / 2:
        data_warnings.append("Fewer than half of the companies have a renewal date.")
    if counts["accounts"] and counts["accounts_with_recorded_champion"] < counts["accounts"] / 2:
        data_warnings.append(
            "Fewer than half of the companies have a champion recorded; Casefile will infer a likely "
            "champion from email and meeting traffic (labelled as inferred)."
        )
    steps = [
        {
            "key": "company",
            "state": state(bool(org.name and org.internal_domains)),
            "data": {"name": org.name, "timezone": org.timezone, "internal_domains": org.internal_domains},
        },
        {
            "key": "permission",
            "state": state(bool(permission)),
            "data": {"statements": permission_statements(org), "recorded": permission},
        },
        {
            "key": "crm",
            "state": state(synced),
            "data": {
                "connections": [_conn_view(session, c) for c in sources],
                "synthetic": any(c.kind == ConnectionKind.fixture for c in sources),
            },
        },
        {
            "key": "usage",
            "state": state(
                counts["usage_days"] > 0
                or any(c.last_success_at for c in usage)
                or bool(settings.get("usage_skipped")),
                optional=True,
            ),
            "data": {
                "connections": [_conn_view(session, c) for c in usage],
                "skipped": bool(settings.get("usage_skipped")),
                "usage_days": counts["usage_days"],
            },
        },
        {
            "key": "data",
            "state": state(synced and counts["accounts"] > 0, warn=bool(data_warnings)),
            "data": {"counts": counts, "warnings": data_warnings},
        },
        {
            "key": "backtest",
            "state": state(backtest is not None, warn=bool(backtest and not backtest["enough_history"])),
            "data": backtest,
        },
        {
            "key": "shadow",
            "state": "done" if mode == "live" else ("active" if mode == "shadow" else "todo"),
            "data": _shadow_stats(session, org),
        },
        {
            "key": "live",
            "state": "done" if mode == "live" else "todo",
            "data": {"checks": live_checks(session, org, now)},
        },
    ]
    blockers = []
    if not permission:
        blockers.append("Record the data permission.")
    if not synced:
        blockers.append("Connect your CRM and wait for the first sync to finish.")
    if backtest is None:
        blockers.append("Run the back-test report.")
    return {
        "mode": mode,
        "mode_since": settings.get("mode_since"),
        "label": data_label(session, org).value,
        "steps": steps,
        "start_shadow_blockers": blockers if mode == "setup" else [],
        "ai_estimate": ai_estimate(session, org) if mode == "setup" else None,
    }


# Measured unit costs (observed in development, CLAUDE.md §8), used only for the estimate below.
COST_PER_EMAIL_READ = 0.00005
COST_PER_EXPLANATION = 0.028
COST_PER_EMAIL_DRAFT = 0.016


def ai_estimate(session: Session, org: Org) -> dict[str, Any]:
    """What starting shadow mode will spend on AI at first (an estimate, labelled as such)."""
    from casefile.ai.email_reader import count_pending_emails
    from casefile.ai.gateway import load_prompt
    from casefile.db.models import Assessment, AssessmentStatus, CaseStatus

    _, version = load_prompt("email_reader")
    emails = count_pending_emails(session, org, version)
    cases = int(
        session.scalar(
            select(func.count())
            .select_from(AccountCase)
            .join(Assessment, Assessment.id == AccountCase.current_assessment_id)
            .where(
                AccountCase.org_id == org.id,
                AccountCase.status == CaseStatus.open,
                Assessment.status == AssessmentStatus.pending,
            )
        )
        or 0
    )
    total = emails * COST_PER_EMAIL_READ + cases * (COST_PER_EXPLANATION + COST_PER_EMAIL_DRAFT)
    cap = get_settings().ai_daily_budget_usd
    return {
        "label": "estimated",
        "emails_to_read": emails,
        "cases_to_explain": cases,
        "usd": round(total, 2),
        "daily_cap_usd": cap,
        "days_at_cap": max(1, -(-int(total * 100) // max(1, int(cap * 100)))) if total else 0,
    }


# ---------------------------------------------------------------- moving between modes


def start_shadow(session: Session, org: Org, user: AppUser) -> None:
    from casefile.worker.jobs import enqueue_recompute

    if blockers := status(session, org)["start_shadow_blockers"]:
        raise OnboardingError(" ".join(blockers))
    _change_mode(session, org, user, "shadow", None)
    session.flush()
    # Work that setup mode held back: read new customer email, explain open cases (then plans follow).
    enqueue(
        session, "read_emails", {"org_id": str(org.id)}, org_id=org.id, dedupe_key=f"read_emails:{org.id}"
    )
    # Rescoring queues an explanation for every open case still without one (then plans follow).
    enqueue_recompute(session, org.id, session.scalars(select(Account.id).where(Account.org_id == org.id)))


def go_live(session: Session, org: Org, user: AppUser, reason: str | None) -> None:
    checks = live_checks(session, org)
    if failed := [c["text"] for c in checks if c["required"] and not c["ok"]]:
        raise OnboardingError("Not yet: " + "; ".join(failed) + ".")
    overridden = [c["key"] for c in checks if not c["required"] and not c["ok"]]
    if overridden and len((reason or "").strip()) < 20:
        raise OnboardingError(
            "Some recommended checks are not met; give a reason (20+ characters) to go live anyway."
        )
    _change_mode(session, org, user, "live", (reason or "").strip() or None, overridden=overridden)


def back_to_shadow(session: Session, org: Org, user: AppUser, reason: str | None) -> None:
    """The off switch: from the next line on, nothing is written (lines already writing finish)."""
    _change_mode(session, org, user, "shadow", (reason or "").strip() or None)


def back_to_setup(session: Session, org: Org, user: AppUser, reason: str | None) -> None:
    _change_mode(session, org, user, "setup", (reason or "").strip() or None)
