"""org deletion receipts

Revision ID: e6c0a4b7d215
Revises: d5b9f3a2c814
Create Date: 2026-10-11 22:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e6c0a4b7d215"
down_revision: str | Sequence[str] | None = "d5b9f3a2c814"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "org_deletion",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_by", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("audit_entries", sa.Integer(), nullable=False),
        sa.Column("audit_chain_ok", sa.Boolean(), nullable=False),
        sa.Column("audit_head_hash", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_deletion")),
    )


def downgrade() -> None:
    op.drop_table("org_deletion")
