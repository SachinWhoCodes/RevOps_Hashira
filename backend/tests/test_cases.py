"""Assessments, case lifecycle and the audit chain on the synced synthetic company."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from casefile.audit.log import verify_chain
from casefile.cases import service
from casefile.cases.service import RecomputeContext, recompute_account, recompute_all
from casefile.db.models import Account, AccountCase, Assessment, AuditLog, CaseKind, CaseStatus, Org
from casefile.scoring import points
from tests.conftest import TEST_TODAY

AS_OF = datetime(TEST_TODAY.year, TEST_TODAY.month, TEST_TODAY.day, 12, 0, tzinfo=UTC)


def _org(s: Session) -> Org:
    return s.scalars(select(Org).where(Org.slug == "test-org")).one()


def _acme(s: Session) -> Account:
    return s.scalars(select(Account).where(Account.name == "Acme Corp", Account.org_id == _org(s).id)).one()


def _count(s: Session, model: type) -> int:
    return s.scalar(select(func.count()).select_from(model).where(model.org_id == _org(s).id)) or 0  # type: ignore[attr-defined]


def test_first_recompute_opens_a_risk_case_for_acme(synced: Session) -> None:
    totals = recompute_all(synced, _org(synced), as_of=AS_OF)
    assert totals["assessed"] > 0
    case = synced.scalars(select(AccountCase).where(AccountCase.account_id == _acme(synced).id)).one()
    assert (case.kind, case.status) == (CaseKind.risk, CaseStatus.open)
    a = synced.get(Assessment, case.current_assessment_id)
    assert a is not None and a.risk_score >= 40
    signals = {s["type"] for s in a.breakdown["signals"]}
    assert {
        "missing_next_step",
        "meeting_gap",
        "champion_silence",
        "usage_decline",
        "renewal_proximity",
    } <= signals


def test_churned_accounts_have_no_open_cases(synced: Session) -> None:
    open_cases = synced.scalars(select(AccountCase).where(AccountCase.status == CaseStatus.open)).all()
    for case in open_cases:
        ctx = RecomputeContext.build(synced, _org(synced))
        assert recompute_account(synced, case.account_id, ctx, as_of=AS_OF).status != "inactive"


def test_second_recompute_changes_nothing(synced: Session) -> None:
    assessments, audits = _count(synced, Assessment), _count(synced, AuditLog)
    totals = recompute_all(synced, _org(synced), as_of=AS_OF)
    assert totals["assessed"] == 0 and totals["opened"] == 0 and totals["resolved"] == 0
    assert _count(synced, Assessment) == assessments
    assert _count(synced, AuditLog) == audits


def test_audit_chain_is_intact(synced: Session) -> None:
    result = verify_chain(synced, _org(synced).id)
    assert result["ok"] and result["checked"] == _count(synced, AuditLog) > 0


def test_rules_change_rechecks_open_cases(synced: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    stricter = {**points.default_scoring(), "version": 99}
    stricter["cases"] = {**stricter["cases"], "open_risk_at": 95, "close_risk_below": 30}
    monkeypatch.setattr(points, "default_scoring", lambda: stricter)
    monkeypatch.setattr(service, "default_scoring", lambda: stricter)
    acme = _acme(synced)
    r = recompute_account(synced, acme.id, RecomputeContext.build(synced, _org(synced)), as_of=AS_OF)
    assert r.status == "assessed"  # new rule version → new assessment
    assert "risk" in r.resolved  # 70 is in the old hysteresis band but below the new opening bar
    case = synced.scalars(select(AccountCase).where(AccountCase.account_id == acme.id)).one()
    assert case.status == CaseStatus.resolved and case.feedback == "auto_resolved"
    assert "updated rules" in (case.feedback_note or "")


def test_tampering_breaks_the_chain(synced: Session) -> None:
    # Even someone who can switch the trigger off cannot edit a row without detection.
    synced.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only"))
    seq = synced.scalar(select(func.min(AuditLog.seq)).where(AuditLog.org_id == _org(synced).id))
    synced.execute(text("UPDATE audit_log SET details = '{\"risk\": 0}' WHERE seq = :s"), {"s": seq})
    synced.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only"))
    result = verify_chain(synced, _org(synced).id)
    assert result["ok"] is False and result["broken_at_seq"] == seq


def test_database_refuses_audit_updates(synced: Session) -> None:
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="append-only"), synced.begin_nested():
        synced.execute(text("UPDATE audit_log SET action = 'x'"))
