from __future__ import annotations

from fastapi import Depends, FastAPI
from starlette.middleware.sessions import SessionMiddleware

from casefile.api.deps import csrf_guard
from casefile.api.routers import (
    accounts,
    auth,
    backtest,
    cases,
    changes,
    connect,
    connections,
    identity,
    insights,
    mapping,
    onboarding,
    queue,
    team,
)
from casefile.config import get_settings

settings = get_settings()

app = FastAPI(title="Casefile API", version="0.1.0", dependencies=[Depends(csrf_guard)])
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret.get_secret_value(),
    session_cookie="casefile_session",
    same_site="lax",
    https_only=settings.app_base_url.startswith("https://"),
    max_age=7 * 24 * 3600,
)

for module in (
    auth,
    accounts,
    connections,
    identity,
    queue,
    insights,
    cases,
    changes,
    backtest,
    team,
    mapping,
    onboarding,
    connect,
):
    app.include_router(module.router, prefix="/api")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/health/ready")
def ready() -> dict[str, str]:
    """Readiness for the load balancer / compose health check: the database answers."""
    from fastapi import HTTPException
    from sqlalchemy import text

    from casefile.db.session import get_sessionmaker

    session = get_sessionmaker()()
    try:
        session.execute(text("select 1"))
    except Exception as exc:  # report, don't crash the probe
        raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc
    finally:
        session.close()
    return {"status": "ready"}
