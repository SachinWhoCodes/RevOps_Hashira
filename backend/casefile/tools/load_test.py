"""Load test (plan Phase 1 gate): N synthetic accounts x D days through the full sync pipeline.

Runs against the TEST database inside one transaction that is rolled back, so dev data is untouched.
Usage: uv run python -m casefile.tools.load_test --accounts 1000 --days 90
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from casefile.bootstrap import ensure_connection, ensure_org
from casefile.config import get_settings
from casefile.db.models import ConnectionKind
from casefile.ingest.sync_runner import run_sync
from casefile.synth.generator import SynthParams, generate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--accounts", type=int, default=1000)
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        t0 = time.perf_counter()
        manifest = generate(
            SynthParams(seed=99, n_accounts=args.accounts, history_days=args.days, today=date.today()),
            Path(tmp),
        )
        t_gen = time.perf_counter() - t0

        engine = create_engine(get_settings().test_database_url)
        with engine.connect() as connection:
            outer = connection.begin()
            session = Session(bind=connection, join_transaction_mode="create_savepoint")
            org = ensure_org(
                session,
                slug="load-test",
                name="Load",
                timezone="Asia/Kolkata",
                internal_domains=["lumora.test"],
            )
            conn = ensure_connection(session, org, ConnectionKind.fixture, name="load", config={"dir": tmp})
            session.commit()
            t1 = time.perf_counter()
            run = run_sync(session, conn.id)
            t_sync = time.perf_counter() - t1
            session.close()
            outer.rollback()
    result = {
        "label": "simulated",
        "accounts": args.accounts,
        "history_days": args.days,
        "records": manifest["counts"],
        "status": run.status.value,
        "records_fetched": run.records_fetched,
        "generate_seconds": round(t_gen, 1),
        "full_sync_seconds": round(t_sync, 1),
        "target_seconds": 15 * 60,
        "passed": run.status.value == "succeeded" and t_sync < 15 * 60,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
