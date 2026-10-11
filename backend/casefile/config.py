"""Runtime settings, read from environment variables and the repo-root .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://casefile:casefile@127.0.0.1:5433/casefile"
    test_database_url: str = "postgresql+psycopg://casefile:casefile@127.0.0.1:5433/casefile_test"
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
    # Encrypts credentials pasted into the app during onboarding (Fernet key; `casefile init-secrets-key`).
    # Read-zone tokens only: write credentials still live only in the executor's own env file (R1).
    secrets_key: SecretStr | None = None
    # Files uploaded during onboarding (usage CSVs). The API and worker must share this directory.
    upload_dir: Path = REPO_ROOT / "data" / "uploads"
    # Public half of the executor's sealing key (X25519, base64): the app can seal write credentials
    # pasted by admins but never open them. The private half lives only in .env.executor.
    executor_seal_public_key: str | None = None
    # Google sign-in for "Connect Google" (web flow). Default: <APP_BASE_URL>/api/oauth/google/callback.
    google_redirect_uri: str | None = None

    # Transactional email (sign-up confirmation, password reset, invites) through Brevo. Without a key,
    # a local (localhost) deployment writes emails to data/outbox instead of sending them.
    brevo_api_key: SecretStr | None = None
    mail_from_email: str | None = None
    mail_from_name: str = "Casefile"
    outbox_dir: Path = REPO_ROOT / "data" / "outbox"
    # Self-service registration limits.
    signup_enabled: bool = True
    signups_per_email_per_hour: int = 3
    signups_per_ip_per_hour: int = 20

    anthropic_api_key: SecretStr | None = None
    # Kill switch: false = deterministic template explanations only, no AI calls at all.
    ai_enabled: bool = True
    # Hard stop on AI spend per organisation per UTC day (cost control, plan section 8). An operator
    # can set a different cap for one organisation (`casefile set-ai-cap`).
    ai_daily_budget_usd: float = 10.0
    # Backstop across all organisations together (self-service sign-up means strangers use our key).
    ai_platform_daily_budget_usd: float = 25.0

    # Phase 4: propose a change set as soon as an open case has a written explanation.
    auto_plan_cases: bool = True

    crm_sync_interval_minutes: int = 15
    gmail_sync_interval_minutes: int = 5
    # A connection is "stale" when its last success is older than this many sync intervals.
    stale_after_intervals: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
