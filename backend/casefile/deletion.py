"""Delete one organisation's data on request (after a demo or a pilot), leaving only a receipt.

What goes: every row of the organisation (the ``org`` row cascades to all tenant tables), its pending
sign-ups, its uploaded files, and its audit chain. What stays: an ``org_deletion`` receipt with
counts and the verified final hash of the deleted audit chain — no names, emails or content.

The audit log is append-only (rule R5), enforced by a trigger. Deleting a whole organisation's chain is
the one exception: done by the database owner, inside one transaction, with triggers paused for that
single statement (``session_replication_role``), after the chain was verified and its head hash
recorded. Other organisations' chains are separate and unaffected (each verifies on its own).

Credentials are deleted with their connection rows. The Google read grant is also revoked at Google
(best effort); HubSpot apps, the Slack app and a Google drafts grant must be removed by the client in
those tools (printed as a checklist), because only the client or the executor can reach them.
"""

from __future__ import annotations

import json
import shutil
import uuid
from typing import Any

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from casefile.audit.log import verify_chain
from casefile.config import get_settings
from casefile.db.models import AuditLog, Base, Connection, ConnectionKind, Org, OrgDeletion, Zone

GOOGLE_REVOKE = "https://oauth2.googleapis.com/revoke"


class DeletionError(RuntimeError):
    pass


def _counts(session: Session, org_id: uuid.UUID) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in Base.metadata.sorted_tables:
        if "org_id" in table.c and table.name not in ("org_deletion",):
            n = session.scalar(select(func.count()).select_from(table).where(table.c.org_id == org_id))
            if n:
                out[table.name] = int(n)
    return out


def _revoke_google(session: Session, org: Org, client: httpx.Client | None) -> list[str]:
    """Revoke Google read grants Casefile holds for this organisation (best effort)."""
    from casefile.connectors.base import AuthError
    from casefile.connectors.registry import connection_secret

    revoked = []
    http = client or httpx.Client(timeout=15)
    for conn in session.scalars(
        select(Connection).where(
            Connection.org_id == org.id, Connection.kind == ConnectionKind.gmail, Connection.zone == Zone.read
        )
    ):
        if not conn.secret_enc:
            continue  # an operator's env token is not the client's to revoke here
        try:
            raw = connection_secret(conn)
            token = json.loads(raw)["refresh_token"] if raw.startswith("{") else raw
            if http.post(GOOGLE_REVOKE, data={"token": token}).status_code == 200:
                revoked.append(conn.name)
        except (AuthError, httpx.HTTPError, KeyError, ValueError):
            continue
    return revoked


def delete_org(
    session: Session,
    slug: str,
    *,
    deleted_by: str,
    reason: str | None = None,
    allow_demo: bool = False,
    google: httpx.Client | None = None,
) -> dict[str, Any]:
    org = session.scalar(select(Org).where(Org.slug == slug))
    if org is None:
        raise DeletionError(f"organisation {slug!r} not found")
    if slug == get_settings().org_slug and not allow_demo:
        raise DeletionError(f"{slug!r} is this server's demo organisation; pass --allow-demo to delete it")
    chain = verify_chain(session, org.id)
    head = session.scalar(
        select(AuditLog.hash).where(AuditLog.org_id == org.id).order_by(AuditLog.seq.desc()).limit(1)
    )
    counts = _counts(session, org.id)
    write_tools = sorted(
        {
            c.kind.value
            for c in session.scalars(
                select(Connection).where(Connection.org_id == org.id, Connection.zone == Zone.write)
            )
        }
    )
    revoked = _revoke_google(session, org, google)
    org_id = org.id
    try:
        # Triggers paused for this one statement only (needs the database owner); FK cascades are
        # triggers too, so they are switched back on before the organisation row is deleted.
        session.execute(text("SET LOCAL session_replication_role = replica"))
        session.execute(text("DELETE FROM audit_log WHERE org_id = :o"), {"o": org_id})
        session.execute(text("SET LOCAL session_replication_role = origin"))
    except Exception as exc:
        raise DeletionError(
            "deleting the audit chain needs the database owner (superuser): run with the owner's DATABASE_URL"
        ) from exc
    session.execute(text("DELETE FROM signup WHERE org_id = :o"), {"o": org_id})
    session.execute(text("DELETE FROM org WHERE id = :o"), {"o": org_id})
    left = _counts(session, org_id)
    if left:
        raise DeletionError(f"rows remain after deletion: {left}")
    receipt = OrgDeletion(
        org_id=org_id,
        slug=slug,
        deleted_by=deleted_by,
        reason=reason,
        counts=counts,
        audit_entries=int(chain.get("total") or 0),
        audit_chain_ok=bool(chain.get("ok")),
        audit_head_hash=head,
    )
    session.add(receipt)
    session.flush()
    uploads = get_settings().upload_dir / str(org_id)
    if uploads.exists():
        shutil.rmtree(uploads)
    still_to_do = []
    if "hubspot" in write_tools or counts.get("connection"):
        still_to_do.append("HubSpot: the client deletes the Casefile private apps (read and write)")
    if "slack" in write_tools:
        still_to_do.append("Slack: the client removes the Casefile app from the workspace")
    if "gmail" in write_tools:
        still_to_do.append(
            "Google: the client removes Casefile at myaccount.google.com/permissions (drafts grant)"
        )
    return {
        "receipt": str(receipt.id),
        "org_id": str(org_id),
        "deleted_rows": sum(counts.values()),
        "by_table": counts,
        "audit_entries": receipt.audit_entries,
        "audit_chain_ok": receipt.audit_chain_ok,
        "audit_head_hash": head,
        "google_grants_revoked": revoked,
        "client_still_to_do": still_to_do,
    }
