"""Executor settings: the only place write-zone credentials are loaded (plan rule R1).

The API and worker read ``Settings`` from ``.env``, which holds no write credentials. The executor
reads ``.env`` (database, org) *and* ``.env.executor`` (write tokens). Run it as its own process.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from casefile.config import REPO_ROOT


class ExecutorSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / ".env.executor"), extra="ignore"
    )

    database_url: str = "postgresql+psycopg://casefile:casefile@localhost:5433/casefile"
    org_slug: str = "lumora"
    app_base_url: str = "http://localhost:5173"

    write_hubspot_token: SecretStr | None = None
    # Read-zone token (from .env), used by the executor only for pre-write checks and marker searches.
    read_hubspot_token: SecretStr | None = None
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    write_google_refresh_token: SecretStr | None = None
    slack_bot_token: SecretStr | None = None
    # Opens write credentials that admins pasted into the app (sealed with the public half, which the app
    # holds as EXECUTOR_SEAL_PUBLIC_KEY). Only this process has it (`casefile init-executor-keys`).
    executor_seal_private_key: SecretStr | None = None

    # Shadow mode: run every check and record "would_execute", but write nothing.
    executor_shadow_mode: bool = False
    executor_poll_seconds: float = 2.0
    # Items stuck in "executing" longer than this are reconciled by searching for their marker.
    executor_reconcile_after_seconds: int = 120
    digest_hour_local: int = 9


@lru_cache
def get_executor_settings() -> ExecutorSettings:
    return ExecutorSettings()
