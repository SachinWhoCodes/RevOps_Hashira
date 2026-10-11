"""app role default privileges

Revision ID: 023a9a2bd48d
Revises: 8db3891a6e59
Create Date: 2026-10-11 05:09:41.617770

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '023a9a2bd48d'
down_revision: Union[str, Sequence[str], None] = '8db3891a6e59'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tables created by later migrations get the same grants as the existing ones, so the app role
    # never silently loses access (and audit_log stays INSERT/SELECT only: it already exists).
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO casefile_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT USAGE, SELECT ON SEQUENCES TO casefile_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO casefile_app;
        REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM casefile_app;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM casefile_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            REVOKE USAGE, SELECT ON SEQUENCES FROM casefile_app;
        """
    )
