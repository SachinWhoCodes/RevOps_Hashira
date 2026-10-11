"""The app's database role can append to the audit log but never change or remove entries."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


def _as_app(db: Session) -> None:
    db.execute(text("SET LOCAL ROLE casefile_app"))


@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log", "TRUNCATE audit_log"],
)
def test_app_role_cannot_rewrite_the_audit_log(db: Session, statement: str) -> None:
    db.execute(text("SAVEPOINT s"))
    _as_app(db)
    with pytest.raises(DBAPIError, match="permission denied"):
        db.execute(text(statement))
    db.execute(text("ROLLBACK TO SAVEPOINT s"))


def test_app_role_can_read_and_use_other_tables(db: Session) -> None:
    _as_app(db)
    db.execute(text("SELECT count(*) FROM audit_log"))
    db.execute(text("SELECT count(*) FROM change_item"))
    assert db.execute(text("SELECT has_table_privilege('casefile_app', 'audit_log', 'INSERT')")).scalar()
    assert db.execute(text("SELECT has_table_privilege('casefile_app', 'external_ref', 'UPDATE')")).scalar()
