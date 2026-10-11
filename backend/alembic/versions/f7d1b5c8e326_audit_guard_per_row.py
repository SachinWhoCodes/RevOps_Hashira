"""audit log guard per row (UPDATE/DELETE) plus per statement (TRUNCATE)

Revision ID: f7d1b5c8e326
Revises: e6c0a4b7d215
Create Date: 2026-10-11 22:30:00

The append-only guard (rule R5) was one statement-level trigger. A statement-level DELETE trigger also
fires for a cascade that deletes *no* audit rows, which made it impossible to delete an organisation
whose chain had already been removed on request (casefile.deletion). Per-row triggers refuse every
UPDATE or DELETE of an existing entry exactly as before; TRUNCATE (which has no rows) stays blocked
per statement.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f7d1b5c8e326"
down_revision: str | Sequence[str] | None = "e6c0a4b7d215"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log")
    op.execute(
        "CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_block_mutation()"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_log_block_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log")
    op.execute(
        "CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_log_block_mutation()"
    )
