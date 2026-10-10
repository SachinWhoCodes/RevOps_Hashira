"""Runtime settings, read from environment variables and the repo-root .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://casefile:casefile@localhost:5433/casefile"
    test_database_url: str = "postgresql+psycopg://casefile:casefile@localhost:5433/casefile_test"
    session_secret: SecretStr = SecretStr("dev-insecure-session-secret-change-me")
    app_base_url: str = "http://localhost:5173"

    org_slug: str = "lumora"
    org_timezone: str = "Asia/Kolkata"

    synth_dir: Path = REPO_ROOT / "data" / "synth"

    # Read-zone credentials only. Write-zone credentials never appear in this class (rule R1).
    read_hubspot_token: SecretStr | None = None
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    read_google_refresh_token: SecretStr | None = None
    read_usage_dsn: SecretStr | None = None

    anthropic_api_key: SecretStr | None = None

    crm_sync_interval_minutes: int = 15
    gmail_sync_interval_minutes: int = 5
    # A connection is "stale" when its last success is older than this many sync intervals.
    stale_after_intervals: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
