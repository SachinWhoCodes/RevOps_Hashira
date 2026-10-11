"""self-serve sign-up: pending registrations, sealed write credentials

Revision ID: d5b9f3a2c814
Revises: c4a8e2f1d903
Create Date: 2026-10-11 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5b9f3a2c814"
down_revision: str | Sequence[str] | None = "c4a8e2f1d903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signup",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("company", sa.String(length=200), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("org_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["org.id"], name=op.f("fk_signup_org_id_org"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signup")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_signup_token_hash")),
    )
    op.create_index(op.f("ix_signup_email"), "signup", ["email"])
    op.create_index(op.f("ix_signup_ip_hash"), "signup", ["ip_hash"])
    op.add_column("connection", sa.Column("secret_sealed", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("connection", "secret_sealed")
    op.drop_index(op.f("ix_signup_ip_hash"), table_name="signup")
    op.drop_index(op.f("ix_signup_email"), table_name="signup")
    op.drop_table("signup")
