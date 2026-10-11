"""rls tenant isolation

Row-level security on every organisation-scoped table (plan Phase 8 / C3). A role that is not the
table owner sees a row only when its org_id equals the transaction's app.org_id, or when
app.bypass_rls is on. With neither set it sees nothing (fail closed). The owner (migrations) and
superusers bypass RLS, as Postgres always does.

Revision ID: b7c1d2e3f4a5
Revises: 534749f39592
Create Date: 2026-10-11
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b7c1d2e3f4a5"
down_revision: Union[str, Sequence[str], None] = "534749f39592"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = [
    "account", "account_case", "activity", "app_user", "approval", "assessment", "audit_log",
    "backtest_run", "change_item", "change_set", "config_version", "connection", "contact", "deal",
    "email_label", "eval_run", "execution_attempt", "external_ref", "field_history",
    "identity_override", "job", "llm_cache", "llm_call", "play_outcome", "policy", "raw_record",
    "renewal_outcome", "signal", "sync_run", "usage_daily", "user_invite",
]
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"
TENANT = "nullif(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({BYPASS} OR org_id = {TENANT}) WITH CHECK ({BYPASS} OR org_id = {TENANT})"
        )
    op.execute("ALTER TABLE org ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON org USING ({BYPASS} OR id = {TENANT}) WITH CHECK ({BYPASS} OR id = {TENANT})")
    op.execute("ALTER TABLE backtest_result ENABLE ROW LEVEL SECURITY")
    run_ok = f"EXISTS (SELECT 1 FROM backtest_run r WHERE r.id = run_id AND r.org_id = {TENANT})"
    op.execute(
        f"CREATE POLICY tenant_isolation ON backtest_result USING ({BYPASS} OR {run_ok}) WITH CHECK ({BYPASS} OR {run_ok})"
    )


def downgrade() -> None:
    for table in [*TENANT_TABLES, "org", "backtest_result"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
