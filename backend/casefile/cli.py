"""Command-line entry point: ``uv run casefile <command>``."""

from __future__ import annotations

import json
import secrets
from datetime import date
from pathlib import Path

import typer
from sqlalchemy import select

from casefile.config import REPO_ROOT, get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """Casefile command-line tools."""


def _set_env(key: str, value: str, file: Path = REPO_ROOT / ".env") -> None:
    """Write KEY=value into a git-ignored env file (replacing an existing line)."""
    lines = file.read_text().splitlines() if file.exists() else []
    lines = [ln for ln in lines if not ln.startswith(f"{key}=")] + [f"{key}={value}"]
    file.write_text("\n".join(lines) + "\n")
    file.chmod(0o600)


@app.command()
def synth(
    seed: int = typer.Option(7, help="Random seed; same seed + same --today gives identical data."),
    accounts: int = typer.Option(200, help="Number of customer accounts."),
    days: int = typer.Option(730, help="History window in days."),
    today: str = typer.Option("", help="Reference date YYYY-MM-DD (default: actual today)."),
    out: Path = typer.Option(None, help="Output directory (default: $SYNTH_DIR/default)."),
) -> None:
    """Generate the synthetic company (labelled 'simulated') in source-native API shapes."""
    from casefile.synth.generator import SynthParams, generate

    ref = date.fromisoformat(today) if today else date.today()
    target = out or get_settings().synth_dir / "default"
    manifest = generate(SynthParams(seed=seed, n_accounts=accounts, history_days=days, today=ref), target)
    typer.echo(json.dumps(manifest["counts"], indent=2))
    typer.echo(f"Wrote {target}")


@app.command()
def bootstrap(
    admin_email: str = typer.Option("", help="Admin login email (default: admin@<internal domain>)."),
    internal_domain: list[str] = typer.Option(None, help="Our own email domain(s); repeatable."),
    fixture_dir: str = typer.Option(
        "data/synth/default", help="Synthetic data dir for the fixture connection."
    ),
    no_fixture: bool = typer.Option(False, help="Do not create the fixture connection (real-data setups)."),
) -> None:
    """Create the organisation, an admin login and (by default) the synthetic-data connection."""
    from casefile.bootstrap import ensure_connection, ensure_org, upsert_user
    from casefile.db.models import ConnectionKind, UserRole
    from casefile.db.session import session_scope

    settings = get_settings()
    domains = list(internal_domain or [])
    org_name = settings.org_slug.title()
    manifest_path = (REPO_ROOT / fixture_dir / "manifest.json") if not no_fixture else None
    if manifest_path and manifest_path.exists():
        vendor = json.loads(manifest_path.read_text())["vendor"]
        domains = domains or [vendor["domain"]]
        org_name = vendor["name"]
    if not domains:
        raise typer.BadParameter("pass --internal-domain (your company's email domain)")
    if (
        not _env_value("SESSION_SECRET")
        or _env_value("SESSION_SECRET") == "change-me-to-a-long-random-string"
    ):
        _set_env("SESSION_SECRET", secrets.token_urlsafe(48))
        typer.echo("Generated SESSION_SECRET in .env.")
    password = _env_value("CASEFILE_ADMIN_PASSWORD")
    if not password:
        password = secrets.token_urlsafe(14)
        _set_env("CASEFILE_ADMIN_PASSWORD", password)
        typer.echo("Generated an admin password and stored it in .env as CASEFILE_ADMIN_PASSWORD.")
    email = admin_email or f"admin@{domains[0]}"
    with session_scope() as session:
        org = ensure_org(
            session,
            slug=settings.org_slug,
            name=org_name,
            timezone=settings.org_timezone,
            internal_domains=domains,
        )
        upsert_user(session, org, email=email, name="Admin", role=UserRole.admin, password=password)
        if not no_fixture:
            ensure_connection(
                session, org, ConnectionKind.fixture, name="synthetic", config={"dir": fixture_dir}
            )
    typer.echo(f"Org '{settings.org_slug}' ready. Admin login: {email} (password in .env).")


def _env_value(key: str) -> str | None:
    import os

    if os.environ.get(key):
        return os.environ[key]
    env = REPO_ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(f"{key}=") and line.split("=", 1)[1].strip():
                return line.split("=", 1)[1].strip()
    return None


@app.command()
def connect(
    kind: str = typer.Argument(..., help="hubspot | gmail | gcal | usage_sql | usage_csv | fixture"),
    name: str = typer.Option("default"),
    config: list[str] = typer.Option(None, help="key=value settings, e.g. path=data/usage.csv"),
    secret_ref: str = typer.Option("", help="Env var holding the secret (defaults per kind)."),
) -> None:
    """Add (or update) a read-zone connection."""
    from casefile.bootstrap import ensure_connection
    from casefile.db.models import ConnectionKind, Org
    from casefile.db.session import session_scope

    cfg = dict(item.split("=", 1) for item in (config or []))
    with session_scope() as session:
        org = session.scalar(select(Org).where(Org.slug == get_settings().org_slug))
        if org is None:
            raise typer.BadParameter("run `casefile bootstrap` first")
        conn = ensure_connection(
            session, org, ConnectionKind(kind), name=name, config=cfg, secret_ref=secret_ref or None
        )
        typer.echo(f"Connection {conn.kind.value}/{conn.name} ready ({conn.id}).")


@app.command()
def sync(
    all_: bool = typer.Option(False, "--all", help="Sync every active read connection."),
    kind: str = typer.Option("", help="Only connections of this kind."),
    name: str = typer.Option("", help="Only the connection with this name."),
) -> None:
    """Run connection syncs now (the worker also runs them on a schedule)."""
    from casefile.db.models import Connection, ConnectionStatus, Org, Zone
    from casefile.db.session import get_sessionmaker
    from casefile.ingest.sync_runner import run_sync

    session = get_sessionmaker()()
    try:
        org = session.scalar(select(Org).where(Org.slug == get_settings().org_slug))
        if org is None:
            raise typer.BadParameter("run `casefile bootstrap` first")
        query = select(Connection).where(
            Connection.org_id == org.id,
            Connection.zone == Zone.read,
            Connection.status != ConnectionStatus.disabled,
        )
        if kind:
            query = query.where(Connection.kind == kind)
        if name:
            query = query.where(Connection.name == name)
        if not (all_ or kind or name):
            raise typer.BadParameter("choose --all, --kind or --name")
        conn_ids = [c.id for c in session.scalars(query)]
        failed = 0
        for conn_id in conn_ids:
            run = run_sync(session, conn_id)
            status = run.status.value
            failed += status != "succeeded"
            typer.echo(
                f"{status:9} pages={run.pages} fetched={run.records_fetched} new={run.records_new}"
                + (f" error={run.error}" if run.error else "")
            )
        if failed:
            raise typer.Exit(1)
    finally:
        session.close()


@app.command("set-password")
def set_password(email: str) -> None:
    """Let a user (e.g. an account owner synced from the CRM) log in."""
    from casefile.bootstrap import hash_password
    from casefile.db.models import AppUser
    from casefile.db.session import session_scope

    password = typer.prompt("New password", hide_input=True, confirmation_prompt=True)
    with session_scope() as session:
        user = session.scalar(select(AppUser).where(AppUser.email == email.lower()))
        if user is None:
            raise typer.BadParameter(f"no user {email}")
        user.password_hash, user.is_active = hash_password(password), True
    typer.echo("Password set.")


@app.command("google-auth")
def google_auth(zone: str = typer.Option("read", help="read | write | seed")) -> None:
    """Sign in as a mailbox owner and store the refresh token for one zone (never printed)."""
    from casefile.connectors.google.auth import interactive_consent

    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise typer.BadParameter("set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env first")
    token = interactive_consent(
        settings.google_client_id, settings.google_client_secret.get_secret_value(), zone
    )
    target = {
        "read": (REPO_ROOT / ".env", "READ_GOOGLE_REFRESH_TOKEN"),
        "write": (REPO_ROOT / ".env.executor", "WRITE_GOOGLE_REFRESH_TOKEN"),
        "seed": (REPO_ROOT / ".env.seed", "SEED_GOOGLE_REFRESH_TOKEN"),
    }[zone]
    _set_env(target[1], token, target[0])
    typer.echo(f"Stored {target[1]} in {target[0].name}.")


@app.command("match-report")
def match_report(truth: Path = typer.Option(None, help="ground_truth.json (default: synthetic dir)")) -> None:
    """Spike S4: identity-resolution accuracy against the synthetic ground truth (label: simulated)."""
    from casefile.db.models import Org
    from casefile.db.session import get_sessionmaker
    from casefile.identity.evaluate import evaluate_matching

    path = truth or get_settings().synth_dir / "default" / "ground_truth.json"
    session = get_sessionmaker()()
    try:
        org = session.scalar(select(Org).where(Org.slug == get_settings().org_slug))
        assert org is not None
        report = evaluate_matching(session, org, json.loads(path.read_text()))
    finally:
        session.close()
    typer.echo(json.dumps(report, indent=2))


if __name__ == "__main__":
    app()
