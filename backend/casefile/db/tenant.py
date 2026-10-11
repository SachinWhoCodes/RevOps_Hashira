"""Row-level security context (C3): which organisation a database session may see.

Postgres row-level security (migration `rls_tenant_isolation`) lets a non-owner role see a row only
when its ``org_id`` equals ``app.org_id`` for the current transaction, or when ``app.bypass_rls`` is
``on``. With neither set, every tenant table looks empty (fail closed).

Both settings are applied with ``set_config(..., true)`` at the start of every transaction from
values kept on the session, so they survive commits inside one request and can never leak to the
next user of a pooled connection.

Who runs how:
- tenant-scoped (default): the API (organisation from the signed session cookie), the slackbot and
  the MCP server. Their only cross-organisation reads — finding a user at login or behind an invite
  link — use ``system_scope`` for that one lookup.
- system (``set_process_system()``): the worker, the executor and the CLI — trusted back-office
  processes that act for every organisation by design.

The database owner (migrations, dev, tests) bypasses row-level security regardless.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.orm import Session

TENANT = "casefile_org_id"
SYSTEM = "casefile_system"
SYSTEM_STACK = "casefile_system_stack"
_UNSET = object()
_process_system = False


def set_process_system(on: bool = True) -> None:
    """Make system mode the default for every session in this process (worker, executor, CLI)."""
    global _process_system
    _process_system = on


def _values(session: Session) -> dict[str, str]:
    system = session.info.get(SYSTEM, _process_system)
    return {"o": session.info.get(TENANT, ""), "b": "on" if system else ""}


def _apply(connection: Any, session: Session) -> None:
    connection.execute(
        text("select set_config('app.org_id', :o, true), set_config('app.bypass_rls', :b, true)"),
        _values(session),
    )


@event.listens_for(Session, "after_begin")
def _on_begin(session: Session, transaction: Any, connection: Any) -> None:
    _apply(connection, session)


def _apply_now(session: Session) -> None:
    if session.in_transaction():
        _apply(session.connection(), session)


def set_tenant(session: Session, org_id: uuid.UUID | str) -> None:
    session.info[TENANT] = str(org_id)
    session.info[SYSTEM] = False
    _apply_now(session)


def system_scope(session: Session, on: bool = True) -> None:
    """Allow this session to read across organisations (``on``), or go back to what it was before the
    matching ``system_scope(session)``. Restoring rather than switching off matters for system-mode
    processes: a helper that widens scope for one query must not leave a worker's session blind."""
    stack: list[Any] = session.info.setdefault(SYSTEM_STACK, [])
    if on:
        stack.append(session.info.get(SYSTEM, _UNSET))
        session.info[SYSTEM] = True
    else:
        previous = stack.pop() if stack else False
        if previous is _UNSET:
            session.info.pop(SYSTEM, None)  # back to the process default
        else:
            session.info[SYSTEM] = previous
    _apply_now(session)
