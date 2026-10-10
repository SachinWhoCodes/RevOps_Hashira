"""Phase 1 end to end: the synthetic company through the real sync pipeline."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.db.models import (
    Account,
    Activity,
    Connection,
    Contact,
    Deal,
    FieldHistory,
    Job,
    MatchMethod,
    Org,
    RawRecord,
    UsageDaily,
)
from casefile.identity.evaluate import evaluate_matching
from casefile.identity.resolver import resolve_identities
from casefile.ingest.sync_runner import run_sync
from casefile.timeline.snapshot import AccountHistory
from tests.conftest import TEST_TODAY


def _org(s: Session) -> Org:
    return s.scalars(select(Org).where(Org.slug == "test-org")).one()


def test_counts_match_the_source_files(synced: Session, small_synth: Path) -> None:
    manifest = json.loads((small_synth / "manifest.json").read_text())["counts"]
    org = _org(synced)
    count = lambda model: synced.scalar(select(func.count()).select_from(model).where(model.org_id == org.id))  # noqa: E731
    assert count(Account) == manifest["hubspot_companies"]
    assert count(Deal) == manifest["hubspot_deals"]
    assert (
        synced.scalar(select(func.count()).where(Contact.org_id == org.id, Contact.origin == "crm"))
        == manifest["hubspot_contacts"]
    )
    assert count(UsageDaily) == manifest["usage_daily"]
    # Emails are stored once per Message-ID even when two owners' mailboxes hold a copy.
    emails = synced.scalar(select(func.count()).where(Activity.org_id == org.id, Activity.source == "gmail"))
    truth = json.loads((small_synth / "ground_truth.json").read_text())["emails"]
    assert emails == len(truth)


def test_resync_is_idempotent(synced: Session) -> None:
    org = _org(synced)
    conn = synced.scalars(select(Connection).where(Connection.org_id == org.id)).one()
    raw_before = synced.scalar(select(func.count()).select_from(RawRecord).where(RawRecord.org_id == org.id))
    acts_before = synced.scalar(select(func.count()).select_from(Activity).where(Activity.org_id == org.id))
    run = run_sync(synced, conn.id)
    assert run.status.value == "succeeded"
    assert run.records_new == 0
    assert (
        synced.scalar(select(func.count()).select_from(RawRecord).where(RawRecord.org_id == org.id))
        == raw_before
    )
    assert (
        synced.scalar(select(func.count()).select_from(Activity).where(Activity.org_id == org.id))
        == acts_before
    )


def test_matching_accuracy_on_synthetic_ground_truth(synced: Session, small_synth: Path) -> None:
    report = evaluate_matching(
        synced, _org(synced), json.loads((small_synth / "ground_truth.json").read_text())
    )
    assert report["label"] == "simulated"
    assert report["wrong"] == 0
    assert report["coverage"] >= 0.95


def test_full_and_incremental_resolution_agree(synced: Session) -> None:
    """Regression: a full re-check must not let a message inherit its own previous thread match."""
    assert resolve_identities(synced, _org(synced), full=True).activities_changed == 0


def test_every_unresolved_record_is_listed_not_guessed(synced: Session) -> None:
    org = _org(synced)
    unresolved = synced.scalars(
        select(Activity).where(
            Activity.org_id == org.id,
            Activity.match_method.in_([MatchMethod.unmatched, MatchMethod.ambiguous]),
        )
    ).all()
    assert all(a.account_id is None for a in unresolved)


def test_dirty_accounts_are_queued_for_recompute(synced: Session) -> None:
    org = _org(synced)
    accounts = synced.scalar(select(func.count()).select_from(Account).where(Account.org_id == org.id))
    queued = synced.scalar(
        select(func.count()).select_from(Job).where(Job.org_id == org.id, Job.kind == "recompute_account")
    )
    assert queued == accounts


def test_acme_snapshot_today(synced: Session) -> None:
    acme = synced.scalars(
        select(Account).where(Account.name == "Acme Corp", Account.org_id == _org(synced).id)
    ).one()
    end_of_today = datetime(TEST_TODAY.year, TEST_TODAY.month, TEST_TODAY.day, 18, 0, tzinfo=UTC)
    snap = AccountHistory.load(synced, acme.id).as_of(end_of_today)
    assert snap is not None
    assert snap.days_to_renewal() == 43
    deal = snap.renewal_deal()
    assert deal is not None and deal.next_step == ""
    meetings = [a for a in snap.activities if a.type == "meeting" and not a.cancelled]
    assert (snap.today - max(m.occurred_at for m in meetings).astimezone(snap.tz).date()).days == 21
    assert not [m for m in meetings if m.occurred_at > snap.as_of]
    last28 = sum(u.active_users for u in snap.usage[-28:])
    prev28 = sum(u.active_users for u in snap.usage[-56:-28])
    assert (last28 - prev28) / prev28 < -0.25  # usage clearly declining


def test_acme_snapshot_in_the_past_hides_the_future(synced: Session) -> None:
    acme = synced.scalars(
        select(Account).where(Account.name == "Acme Corp", Account.org_id == _org(synced).id)
    ).one()
    t = datetime(TEST_TODAY.year, TEST_TODAY.month, TEST_TODAY.day, tzinfo=UTC) - timedelta(days=100)
    snap = AccountHistory.load(synced, acme.id).as_of(t)
    assert snap is not None
    assert all(a.known_at <= t for a in snap.activities)
    assert all(u.date < t.date() for u in snap.usage)
    assert snap.renewal_deal() is None  # the 2026 renewal deal was created later


def test_field_history_is_written_for_deals(synced: Session) -> None:
    org = _org(synced)
    fields = set(synced.scalars(select(FieldHistory.field).where(FieldHistory.org_id == org.id).distinct()))
    assert {"stage", "amount", "close_date", "next_step", "renewal_date", "role", "status"} <= fields
