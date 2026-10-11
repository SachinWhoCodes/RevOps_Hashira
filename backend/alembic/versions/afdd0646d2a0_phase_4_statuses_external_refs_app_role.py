"""phase 4 statuses, external refs, app role

Revision ID: afdd0646d2a0
Revises: d2475270c5c9
Create Date: 2026-10-11 04:39:34.664253

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'afdd0646d2a0'
down_revision: Union[str, Sequence[str], None] = 'd2475270c5c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ITEM_STATUSES = ["proposed", "approved", "edited_approved", "rejected", "queued", "executing", "succeeded",
                 "failed", "conflict", "would_execute"]
SET_STATUSES = ["proposed", "partially_approved", "approved", "executing", "done", "failed", "rejected"]


def _check(values: list[str], column: str = "status") -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_change_item_changeitemstatus"), "change_item", type_="check")
    op.create_check_constraint(
        op.f("ck_change_item_changeitemstatus"), "change_item", _check([*ITEM_STATUSES, "blocked", "superseded"])
    )
    op.drop_constraint(op.f("ck_change_set_changesetstatus"), "change_set", type_="check")
    op.create_check_constraint(op.f("ck_change_set_changesetstatus"), "change_set", _check([*SET_STATUSES, "superseded"]))

    op.create_table(
        "external_ref",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("system", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["org.id"], name=op.f("fk_external_ref_org_id_org"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_ref")),
        sa.UniqueConstraint("org_id", "entity_type", "entity_id", "system",
                            name=op.f("uq_external_ref_org_id_entity_type_entity_id_system")),
    )
    op.create_index(op.f("ix_external_ref_org_id"), "external_ref", ["org_id"], unique=False)

    # Least-privilege role for the app processes (ADR 0001 item 8). It may read and append to the
    # audit log but never update, delete or truncate it, and as a non-owner it cannot disable the
    # append-only trigger. Production logins are created IN ROLE casefile_app.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'casefile_app') THEN
                CREATE ROLE casefile_app NOLOGIN;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO casefile_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO casefile_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO casefile_app;
        REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM casefile_app;
        """
    )


def downgrade() -> None:
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM casefile_app")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM casefile_app")
    op.execute("REVOKE USAGE ON SCHEMA public FROM casefile_app")
    op.drop_index(op.f("ix_external_ref_org_id"), table_name="external_ref")
    op.drop_table("external_ref")
    op.drop_constraint(op.f("ck_change_set_changesetstatus"), "change_set", type_="check")
    op.create_check_constraint(op.f("ck_change_set_changesetstatus"), "change_set", _check(SET_STATUSES))
    op.drop_constraint(op.f("ck_change_item_changeitemstatus"), "change_item", type_="check")
    op.create_check_constraint(op.f("ck_change_item_changeitemstatus"), "change_item", _check(ITEM_STATUSES))
