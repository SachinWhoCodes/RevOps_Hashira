"""onboarding: encrypted connection secrets, explicit secret refs, organisation modes

Revision ID: c4a8e2f1d903
Revises: b7c1d2e3f4a5
Create Date: 2026-10-11 18:00:00

- ``connection.secret_enc``: a credential pasted during onboarding, encrypted with SECRETS_KEY.
- Connections that relied on an implicit per-kind env var get it written down, because the implicit
  default is gone: with several organisations it would hand one company's token to another.
- Organisations that predate onboarding are marked ``live`` (they already write); new ones start in
  ``setup`` and go through shadow mode.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4a8e2f1d903"
down_revision: str | Sequence[str] | None = "b7c1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_REFS = {
    "hubspot": "READ_HUBSPOT_TOKEN",
    "gmail": "READ_GOOGLE_REFRESH_TOKEN",
    "gcal": "READ_GOOGLE_REFRESH_TOKEN",
    "usage_sql": "READ_USAGE_DSN",
}


def upgrade() -> None:
    op.add_column("connection", sa.Column("secret_enc", sa.Text(), nullable=True))
    for kind, ref in DEFAULT_REFS.items():
        op.execute(
            sa.text(
                "update connection set secret_ref = :ref "
                "where zone = 'read' and secret_ref is null and kind = :kind"
            ).bindparams(ref=ref, kind=kind)
        )
    op.execute(
        "update org set settings = coalesce(settings, '{}'::jsonb) || '{\"mode\": \"live\"}'::jsonb "
        "where not (coalesce(settings, '{}'::jsonb) ? 'mode')"
    )


def downgrade() -> None:
    op.execute("update org set settings = settings - 'mode'")
    op.drop_column("connection", "secret_enc")
