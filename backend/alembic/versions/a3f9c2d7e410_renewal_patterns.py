"""renewal patterns: pattern library, finished-cycle patterns, current-client matches

Revision ID: a3f9c2d7e410
Revises: f7d1b5c8e326
Create Date: 2026-10-12 10:00:00

Three tenant tables for casefile.patterns, with the same row-level security policy as every other
tenant table (b7c1d2e3f4a5). Grants come from the default privileges set in 023a9a2bd48d.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3f9c2d7e410"
down_revision: str | Sequence[str] | None = "f7d1b5c8e326"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ["pattern_model", "cycle_pattern", "pattern_match"]
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"
TENANT = "nullif(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "pattern_model",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "label",
            sa.Enum(
                "observed",
                "simulated",
                "estimated",
                "untested",
                name="evidencelabel",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "label IN ('observed', 'simulated', 'estimated', 'untested')",
            name=op.f("ck_pattern_model_evidencelabel"),
        ),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evaluation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["org.id"], name=op.f("fk_pattern_model_org_id_org"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pattern_model")),
        sa.UniqueConstraint("org_id", "version", name=op.f("uq_pattern_model_org_id_version")),
    )
    op.create_index(op.f("ix_pattern_model_org_id"), "pattern_model", ["org_id"], unique=False)

    op.create_table(
        "cycle_pattern",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("model_id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("outcome_id", sa.UUID(), nullable=True),
        sa.Column("cycle_start", sa.Date(), nullable=True),
        sa.Column("cycle_end", sa.Date(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("arr_before", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("arr_after", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("points", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["account.id"],
            name=op.f("fk_cycle_pattern_account_id_account"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["pattern_model.id"],
            name=op.f("fk_cycle_pattern_model_id_pattern_model"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["org.id"], name=op.f("fk_cycle_pattern_org_id_org"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["outcome_id"],
            ["renewal_outcome.id"],
            name=op.f("fk_cycle_pattern_outcome_id_renewal_outcome"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cycle_pattern")),
    )
    op.create_index(op.f("ix_cycle_pattern_org_id"), "cycle_pattern", ["org_id"], unique=False)
    op.create_index(op.f("ix_cycle_pattern_model_id"), "cycle_pattern", ["model_id"], unique=False)

    op.create_table(
        "pattern_match",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("model_id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewal_date", sa.Date(), nullable=True),
        sa.Column("days_to_renewal", sa.Integer(), nullable=True),
        sa.Column("stage", sa.Integer(), nullable=True),
        sa.Column("verdict", sa.String(length=16), nullable=True),
        sa.Column("lost", sa.Integer(), nullable=True),
        sa.Column("k", sa.Integer(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["account.id"],
            name=op.f("fk_pattern_match_account_id_account"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["pattern_model.id"],
            name=op.f("fk_pattern_match_model_id_pattern_model"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["org.id"], name=op.f("fk_pattern_match_org_id_org"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pattern_match")),
        sa.UniqueConstraint("model_id", "account_id", name=op.f("uq_pattern_match_model_id_account_id")),
    )
    op.create_index(op.f("ix_pattern_match_org_id"), "pattern_match", ["org_id"], unique=False)

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({BYPASS} OR org_id = {TENANT}) WITH CHECK ({BYPASS} OR org_id = {TENANT})"
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.drop_index(op.f("ix_pattern_match_org_id"), table_name="pattern_match")
    op.drop_table("pattern_match")
    op.drop_index(op.f("ix_cycle_pattern_model_id"), table_name="cycle_pattern")
    op.drop_index(op.f("ix_cycle_pattern_org_id"), table_name="cycle_pattern")
    op.drop_table("cycle_pattern")
    op.drop_index(op.f("ix_pattern_model_org_id"), table_name="pattern_model")
    op.drop_table("pattern_model")
