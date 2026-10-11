"""Command-line entry point: ``uv run casefile <command>``."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import typer
from sqlalchemy import select

from casefile.config import REPO_ROOT, get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """Casefile command-line tools."""
    from casefile.db.tenant import set_process_system

    set_process_system()  # the CLI is an operator tool: it acts across organisations


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
            # The synthetic demo company is live against the sandbox tools; a real one starts in setup.
            mode="setup" if no_fixture else "live",
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
        default_ref = {"hubspot": "READ_HUBSPOT_TOKEN", "gmail": "READ_GOOGLE_REFRESH_TOKEN"}
        default_ref |= {"gcal": "READ_GOOGLE_REFRESH_TOKEN", "usage_sql": "READ_USAGE_DSN"}
        conn = ensure_connection(
            session,
            org,
            ConnectionKind(kind),
            name=name,
            config=cfg,
            secret_ref=secret_ref or default_ref.get(kind),  # written down: there is no implicit default
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


@app.command()
def recompute(account: str = typer.Option("", help="Only this account (name).")) -> None:
    """Recompute signals, scores, assessments and cases (the worker also does this)."""
    from casefile.cases.service import RecomputeContext, recompute_account, recompute_all
    from casefile.db.models import Account, Org
    from casefile.db.session import get_sessionmaker

    session = get_sessionmaker()()
    try:
        org = session.scalar(select(Org).where(Org.slug == get_settings().org_slug))
        if org is None:
            raise typer.BadParameter("run `casefile bootstrap` first")
        if account:
            acc = session.scalar(select(Account).where(Account.org_id == org.id, Account.name == account))
            if acc is None:
                raise typer.BadParameter(f"no account named {account!r}")
            r = recompute_account(session, acc.id, RecomputeContext.build(session, org))
            session.commit()
            typer.echo(f"{r.status} opened={r.opened} resolved={r.resolved}")
        else:
            typer.echo(json.dumps(recompute_all(session, org)))
    finally:
        session.close()


@app.command("purge-bodies")
def purge_bodies() -> None:
    """Delete full email text past its retention date (snippets stay). The worker runs this too."""
    from casefile.db.models import Org
    from casefile.db.session import get_sessionmaker
    from casefile.ingest.retention import purge_expired_bodies

    session = get_sessionmaker()()
    try:
        for org in session.scalars(select(Org)).all():
            result = purge_expired_bodies(session, org)
            session.commit()
            typer.echo(f"{org.slug}: {json.dumps(result)}")
    finally:
        session.close()


@app.command()
def outcomes() -> None:
    """Outcome tracker: record and measure +7/+14/+30-day checkpoints after executed plays."""
    from casefile.db.models import Org
    from casefile.db.session import get_sessionmaker
    from casefile.outcomes.tracker import plays_table, track

    session = get_sessionmaker()()
    try:
        for org in session.scalars(select(Org)).all():
            counts = track(session, org)
            session.commit()
            typer.echo(f"{org.slug}: {json.dumps(counts)}")
            for play in plays_table(session, org):
                cps = ", ".join(
                    f"d{c['day']}: {c['improved']}↑ {c['no_change']}= {c['worse']}↓ {c['pending']}…"
                    for c in play["checkpoints"]
                )
                typer.echo(f"  {play['play']} ({play['sets']} sets) — {cps}")
    finally:
        session.close()


@app.command()
def ask(
    account: str = typer.Option(..., help="Account name."),
    question: str = typer.Argument(..., help="The question, in quotes."),
) -> None:
    """Ask Claude about an account (read-only tools; every sentence checked). Prints the cost."""
    from casefile.ai.gateway import AiGateway
    from casefile.ai.qa import ask as ask_account
    from casefile.cases.service import RecomputeContext, assess
    from casefile.db.models import Account
    from casefile.timeline.snapshot import AccountHistory

    session, org = _org_session()
    try:
        acc = session.scalar(select(Account).where(Account.org_id == org.id, Account.name == account))
        if acc is None:
            raise typer.BadParameter(f"no account named {account!r}")
        snap = AccountHistory.load(session, acc.id).as_of(datetime.now(UTC))
        if snap is None:
            raise typer.BadParameter(f"{account!r} has no data yet")
        ctx = RecomputeContext.build(session, org)
        result = ask_account(
            session, AiGateway(session, org.id), snap, assess(snap, arr_fallback=ctx.arr_median), question
        )
        session.commit()
        for s in result.sentences:
            typer.echo(f"  {s['text']}  [{', '.join(e['eid'] for e in s['evidence'])}]")
        if result.gaps:
            typer.echo(f"  (gaps) {result.gaps}")
        for r in result.removed:
            typer.echo(f"  REMOVED: {r['text']} — {'; '.join(r['reasons'])}")
        typer.echo(
            f"ok={result.ok} error={result.error} tools={result.tools_used} turns={result.iterations} "
            f"cached={result.cached} cost_usd={result.cost_usd:.5f}"
        )
    finally:
        session.close()


@app.command("qa-eval")
def qa_eval() -> None:
    """Run the scripted account questions (Phase 6.4 exit test). Costs money unless cached."""
    from casefile.ai.gateway import AiGateway
    from casefile.evals.qa_eval import run_qa_eval

    session, org = _org_session()
    try:
        m = run_qa_eval(session, org, AiGateway(session, org.id))
        for r in m["rows"]:
            mark = "PASS" if r["passed"] else "FAIL"
            typer.echo(
                f"{mark} {r['id']} {r['account']}: {r.get('verified', 0)} shown, "
                f"{r.get('removed', 0)} removed, "
                f"tools={r.get('tools')} ${r.get('cost_usd', 0)}{' (cached)' if r.get('cached') else ''}"
                f"{' ERROR ' + str(r['error']) if r.get('error') else ''}"
            )
        typer.echo(json.dumps({k: v for k, v in m.items() if k != "rows"}))
    finally:
        session.close()


@app.command("link-slack")
def link_slack(
    email: str = typer.Option(..., help="Casefile user's email."),
    slack_user: str = typer.Option(..., help="Their Slack member id (U…)."),
) -> None:
    """Link a Casefile user to their Slack account, so their clicks on approval buttons count."""
    from casefile.audit.log import append as audit
    from casefile.db.models import AppUser

    session, org = _org_session()
    try:
        user = session.scalar(select(AppUser).where(AppUser.org_id == org.id, AppUser.email == email.lower()))
        if user is None:
            raise typer.BadParameter(f"no user {email!r}")
        user.slack_user_id = slack_user.strip()
        audit(
            session,
            org.id,
            "user_slack_linked",
            entity_type="user",
            entity_id=str(user.id),
            details={"slack_user_id": user.slack_user_id},
        )
        session.commit()
        typer.echo(f"{user.email} linked to Slack user {user.slack_user_id}")
    finally:
        session.close()


@app.command("init-secrets-key")
def init_secrets_key(
    rotate: bool = typer.Option(False, help="Replace an existing key (stored tokens stop working)."),
) -> None:
    """Create SECRETS_KEY in .env, used to encrypt tokens admins paste during onboarding. Never printed."""
    from casefile.secretbox import new_key

    if _env_value("SECRETS_KEY") and not rotate:
        typer.echo("SECRETS_KEY already set; nothing changed.")
        return
    _set_env("SECRETS_KEY", new_key())
    typer.echo(
        "SECRETS_KEY written to .env (back it up with the database: without it, stored tokens are lost)."
    )


@app.command("init-executor-keys")
def init_executor_keys(
    rotate: bool = typer.Option(
        False, help="Replace existing keys (every sealed write credential stops working)."
    ),
) -> None:
    """Create the executor's sealing key pair: private half to .env.executor, public half to .env.

    The web app seals write credentials that admins paste with the public half; only the executor can
    open them. Nothing is printed."""
    from casefile.sealing import new_keypair

    executor_env = REPO_ROOT / ".env.executor"
    if _env_value("EXECUTOR_SEAL_PUBLIC_KEY") and not rotate:
        typer.echo("EXECUTOR_SEAL_PUBLIC_KEY already set; nothing changed.")
        return
    private, public = new_keypair()
    _set_env("EXECUTOR_SEAL_PRIVATE_KEY", private, executor_env)
    _set_env("EXECUTOR_SEAL_PUBLIC_KEY", public)
    typer.echo("Sealing keys written: private half to .env.executor, public half to .env (back up both).")


@app.command("set-secret")
def set_secret(
    key: str = typer.Argument(..., help="e.g. BREVO_API_KEY"),
    file: str = typer.Option(".env", help=".env, .env.executor or .env.slackbot"),
) -> None:
    """Store a secret in an env file without it appearing on screen or in shell history (hidden prompt)."""
    import re

    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", key):
        raise typer.BadParameter("KEY must be UPPER_SNAKE_CASE")
    if file not in {".env", ".env.executor", ".env.slackbot"}:
        raise typer.BadParameter("file must be .env, .env.executor or .env.slackbot")
    value = typer.prompt(f"{key}", hide_input=True).strip()
    if not value or "\n" in value:
        raise typer.BadParameter("empty value")
    _set_env(key, value, REPO_ROOT / file)
    typer.echo(f"{key} saved to {file} ({len(value)} characters).")


@app.command("set-ai-cap")
def set_ai_cap(
    org: str = typer.Option(..., help="Organisation slug"),
    usd: float = typer.Option(..., help="Daily AI budget in USD for this organisation (0 stops AI)."),
) -> None:
    """Give one organisation its own daily AI cap (default: AI_DAILY_BUDGET_USD)."""
    from casefile.audit.log import append as audit
    from casefile.db.models import Org
    from casefile.db.session import session_scope

    if not 0 <= usd <= 1000:
        raise typer.BadParameter("usd must be between 0 and 1000")
    with session_scope() as session:
        row = session.scalar(select(Org).where(Org.slug == org))
        if row is None:
            raise typer.BadParameter(f"organisation {org!r} not found")
        before = (row.settings or {}).get("ai_daily_budget_usd")
        row.settings = {**(row.settings or {}), "ai_daily_budget_usd": usd}
        audit(
            session,
            row.id,
            "ai_cap_changed",
            actor_type="system",
            actor_id="cli:set-ai-cap",
            entity_type="org",
            entity_id=str(row.id),
            details={"from": before, "to": usd},
        )
    typer.echo(f"{org}: daily AI cap ${usd:.2f}")


@app.command("create-org")
def create_org(
    slug: str = typer.Option(..., help="Short id, e.g. acme"),
    name: str = typer.Option(..., help="Company name"),
    admin_email: str = typer.Option(..., help="The company's first admin; gets a one-time invite link."),
    admin_name: str = typer.Option("", help="Their name"),
    internal_domain: list[str] = typer.Option(None, help="The company's own email domain(s); repeatable."),
    timezone: str = typer.Option("UTC", help="IANA time zone, e.g. Asia/Kolkata"),
) -> None:
    """Onboard a new company: an organisation in setup mode (no AI, no writes) and its first admin.

    Prints the admin's invite link once — send it to them yourself. They finish onboarding in the app.
    """
    import re
    from zoneinfo import ZoneInfo

    from casefile.bootstrap import ensure_org
    from casefile.db.models import AppUser, Org, UserRole
    from casefile.db.session import session_scope
    from casefile.invites import email_in_other_org, issue_invite

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", slug):
        raise typer.BadParameter("slug: lowercase letters, digits and dashes")
    ZoneInfo(timezone)
    email = admin_email.strip().lower()
    with session_scope() as session:
        if session.scalar(select(Org).where(Org.slug == slug)):
            raise typer.BadParameter(f"organisation {slug!r} already exists")
        if email_in_other_org(session, email, None):
            raise typer.BadParameter("that email already has a login with another organisation")
        domains = [d.lower() for d in internal_domain or []] or [email.split("@", 1)[1]]
        org = ensure_org(
            session, slug=slug, name=name, timezone=timezone, internal_domains=domains, mode="setup"
        )
        user = AppUser(org_id=org.id, email=email, name=admin_name, role=UserRole.admin, is_active=False)
        session.add(user)
        session.flush()
        link, expires = issue_invite(
            session, user, actor_type="system", actor_id="cli:create-org", created_by=None
        )
    typer.echo(f"Organisation {slug!r} created in setup mode (no AI spend, no writes).")
    typer.echo(f"Invite for {email} — works once, expires {expires:%Y-%m-%d %H:%M} UTC. Send it yourself:")
    typer.echo(link)


@app.command("db-roles")
def db_roles(login: str = typer.Option("casefile_runtime", help="Login role the app processes use.")) -> None:
    """Create or update the runtime database login (member of casefile_app: no UPDATE/DELETE on the
    audit log). Password from APP_DB_PASSWORD. Run as the owner, after migrations (deploy step)."""
    import os
    import re

    from sqlalchemy import create_engine, text

    password = os.environ.get("APP_DB_PASSWORD", "")
    # URL-safe only (what `secrets.token_urlsafe` makes): Postgres takes no bind parameters in
    # ALTER ROLE ... PASSWORD, so the value must need no quoting at all.
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", password):
        raise typer.BadParameter("APP_DB_PASSWORD must be 16+ URL-safe characters (A-Z a-z 0-9 _ -)")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{2,40}", login):
        raise typer.BadParameter("invalid role name")
    engine = create_engine(get_settings().database_url)
    with engine.begin() as conn:
        exists = conn.execute(text("select 1 from pg_roles where rolname = :r"), {"r": login}).first()
        verb = "ALTER" if exists else "CREATE"
        conn.exec_driver_sql(f"{verb} ROLE {login} LOGIN PASSWORD '{password}'")
        conn.exec_driver_sql(f"GRANT casefile_app TO {login}")
    typer.echo(f"{login}: {'updated' if exists else 'created'} (member of casefile_app)")


@app.command("reapply-mapping")
def reapply_mapping_cmd() -> None:
    """Rebuild stored data with the organisation's current field mapping (no AI, no re-fetch)."""
    from casefile.ingest.mapping import reapply

    session, org = _org_session()
    try:
        out = reapply(session, org.id)
        session.commit()
        typer.echo(json.dumps(out))
    finally:
        session.close()


@app.command("ranking-report")
def ranking_report(
    truth: Path = typer.Option(None, help="ground_truth.json (default: synthetic dir)"),
) -> None:
    """How well today's ranking surfaces the planted at-risk accounts (label: simulated)."""
    from casefile.db.models import Org
    from casefile.db.session import get_sessionmaker
    from casefile.scoring.evaluate import evaluate_ranking

    path = truth or get_settings().synth_dir / "default" / "ground_truth.json"
    session = get_sessionmaker()()
    try:
        org = session.scalar(select(Org).where(Org.slug == get_settings().org_slug))
        assert org is not None
        typer.echo(json.dumps(evaluate_ranking(session, org, json.loads(path.read_text())), indent=2))
    finally:
        session.close()


def _org_session() -> tuple[Any, Any]:
    from casefile.db.models import Org
    from casefile.db.session import get_sessionmaker

    session = get_sessionmaker()()
    org = session.scalar(select(Org).where(Org.slug == get_settings().org_slug))
    if org is None:
        session.close()
        raise typer.BadParameter("run `casefile bootstrap` first")
    return session, org


@app.command("scan-injections")
def scan_injections_cmd() -> None:
    """Run the rule-based injection detector over inbound emails (no AI, no cost)."""
    from casefile.ai.injection import scan_injections

    session, org = _org_session()
    try:
        flagged = scan_injections(session, org)
        session.commit()
        typer.echo(f"accounts with flagged emails: {len(flagged)}")
    finally:
        session.close()


@app.command("read-emails")
def read_emails_cmd(
    limit: int = typer.Option(100, help="Maximum emails to read in this run (cost control)."),
    since_days: int = typer.Option(120, help="Only emails from the last N days."),
    open_cases_only: bool = typer.Option(
        True, help="Only accounts with an open case (cheapest useful scope)."
    ),
    batch: bool = typer.Option(False, help="Use the Message Batches API (half price, can take up to hours)."),
) -> None:
    """AI email reader (Claude Haiku 5.5) over pending inbound customer emails. Prints the cost."""
    from casefile.ai.email_reader import pending_emails, read_emails, read_emails_batch
    from casefile.ai.explain import open_case_assessments
    from casefile.ai.gateway import AiGateway, load_prompt
    from casefile.db.models import Assessment
    from casefile.worker.jobs import enqueue_recompute

    session, org = _org_session()
    try:
        gateway = AiGateway(session, org.id)
        before = gateway.spent_today()
        _, version = load_prompt("email_reader")
        emails = pending_emails(session, org, version, since_days=since_days)
        if open_cases_only:
            accounts = {session.get(Assessment, a).account_id for a in open_case_assessments(session, org)}
            emails = [e for e in emails if e.account_id in accounts]
        emails = emails[:limit]
        run = read_emails_batch if batch else read_emails
        out = run(session, org, gateway, emails)
        enqueue_recompute(session, org.id, out.accounts)
        session.commit()
        typer.echo(
            json.dumps(
                {
                    **{k: v for k, v in vars(out).items() if k != "accounts"},
                    "accounts": len(out.accounts),
                    "cost_usd": round(gateway.spent_today() - before, 5),
                },
                indent=2,
            )
        )
    finally:
        session.close()


@app.command()
def explain(
    open_cases: bool = typer.Option(True, help="Explain every open case's current assessment."),
    account: str = typer.Option("", help="Only this account (name)."),
    no_ai: bool = typer.Option(False, help="Template explanations only (no AI, no cost)."),
) -> None:
    """Write and check case explanations (Claude Opus 5.5 + code checker). Prints the cost."""
    from casefile.ai.explain import explain_assessment, open_case_assessments
    from casefile.ai.gateway import AiGateway
    from casefile.db.models import Account, AccountCase, CaseStatus

    session, org = _org_session()
    try:
        gateway = None if no_ai else AiGateway(session, org.id)
        before = gateway.spent_today() if gateway else 0.0
        ids = open_case_assessments(session, org) if open_cases else []
        if account:
            acc = session.scalar(select(Account).where(Account.org_id == org.id, Account.name == account))
            if acc is None:
                raise typer.BadParameter(f"no account named {account!r}")
            ids = [
                c.current_assessment_id
                for c in session.scalars(
                    select(AccountCase).where(
                        AccountCase.account_id == acc.id, AccountCase.status == CaseStatus.open
                    )
                )
                if c.current_assessment_id
            ]
        for aid in dict.fromkeys(ids):
            w = explain_assessment(session, aid, gateway, force=True)
            session.commit()
            if w is None:
                typer.echo(f"{aid}: skipped (assessment superseded)")
                continue
            removed = len(w.report.get("removed", []))
            typer.echo(f"{w.status:9} claims={len(w.claims)} removed={removed} | {w.headline}")
        if gateway:
            typer.echo(f"cost_usd: {gateway.spent_today() - before:.5f}")
    finally:
        session.close()


@app.command("eval")
def eval_cmd(
    suite: str = typer.Argument(..., help="injection | email-labels"),
    no_ai: bool = typer.Option(False, help="Rules only (injection suite), no cost."),
) -> None:
    """Run an evaluation and save results to evals/results/ (quality: simulated; cost: observed)."""
    from casefile.ai.gateway import AiGateway
    from casefile.evals.ai_evals import eval_email_labels, eval_injection, write_results

    session, org = _org_session()
    try:
        gateway = None if no_ai else AiGateway(session, org.id)
        if suite == "injection":
            metrics = eval_injection(session, org, gateway)
        elif suite == "email-labels":
            if gateway is None:
                raise typer.BadParameter("the email-label eval needs the AI")
            truth = json.loads((get_settings().synth_dir / "default" / "ground_truth.json").read_text())
            metrics = eval_email_labels(session, org, gateway, truth)
        else:
            raise typer.BadParameter(f"unknown suite {suite}")
        session.commit()
        typer.echo(json.dumps(metrics, indent=2, default=str))
        typer.echo(f"saved {write_results(suite, metrics)}")
    finally:
        session.close()


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


@app.command("derive-outcomes")
def derive_outcomes_cmd() -> None:
    """Label past renewals (renewed / downgraded / churned / late) from CRM deal history."""
    from casefile.backtest.outcomes import derive_outcomes

    session, org = _org_session()
    try:
        report = derive_outcomes(session, org)
        session.commit()
        typer.echo(json.dumps(report.__dict__, indent=2))
    finally:
        session.close()


@app.command()
def backtest(
    split: str = typer.Option("", help="Tune on renewals before this date (default: median)."),
    label: str = typer.Option("simulated", help="observed | simulated (synthetic data is simulated)."),
) -> None:
    """Time-machine back-test over past renewals; prints the metrics and stores the run."""
    from casefile.backtest.outcomes import derive_outcomes
    from casefile.backtest.runner import BacktestConfig, run_backtest
    from casefile.db.models import EvidenceLabel

    session, org = _org_session()
    try:
        derive_outcomes(session, org)
        cfg = BacktestConfig(
            split_date=date.fromisoformat(split) if split else None, label=EvidenceLabel(label)
        )
        run = run_backtest(session, org, cfg)
        session.commit()
        typer.echo(json.dumps({"run_id": str(run.id), **run.metrics}, indent=2, default=str))
    finally:
        session.close()


@app.command()
def evals(
    ai: bool = typer.Option(
        False, help="Also run the AI suites (costs money; capped by AI_DAILY_BUDGET_USD)."
    ),
    tests: bool = typer.Option(True, help="Run the test-backed suites (pytest on the test database)."),
    out: Path = typer.Option(None, help="Also write the markdown table here (e.g. docs/RESULTS.md)."),
) -> None:
    """Run every evaluation suite; record eval_run rows and print a labelled markdown table."""
    from casefile.evals.run_evals import run_all

    session, org = _org_session()
    try:
        results, markdown = run_all(session, org, ai=ai, tests=tests)
        session.commit()
    finally:
        session.close()
    results_dir = REPO_ROOT / "evals" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    (results_dir / f"evals-{stamp}.md").write_text(markdown)
    if out:
        out.write_text(markdown)
    typer.echo(markdown)
    if any(not r.ok for r in results):
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
