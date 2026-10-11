"""Phase 6.8: Casefile's read-only MCP server, exercised through the official MCP client in-process."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from mcp import Client
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.ai.explain import explain_assessment, open_case_assessments
from casefile.cases.service import recompute_all
from casefile.db.models import Account, AccountCase, Activity, Assessment, CaseStatus, Org
from casefile.mcp_server.server import create_server
from tests.conftest import TEST_TODAY

NOW = datetime(TEST_TODAY.year, TEST_TODAY.month, TEST_TODAY.day, 12, tzinfo=UTC)


@pytest.fixture(scope="module")
def org(synced: Session) -> Iterator[Org]:
    org = synced.scalars(select(Org).where(Org.slug == "test-org")).one()
    recompute_all(synced, org, as_of=NOW)
    for aid in open_case_assessments(synced, org):
        explain_assessment(synced, aid, None)  # template explanations, no AI
    synced.commit()
    yield org


def call(session: Session, name: str, args: dict[str, Any], user: str = "admin@lumora.test") -> Any:
    server = create_server(lambda: session, user, base_url="http://x", close_sessions=False)

    async def go() -> Any:
        async with Client(server) as client:
            return await client.call_tool(name, args)

    return asyncio.run(go())


def test_three_tools_all_read_only(synced: Session, org: Org) -> None:
    server = create_server(lambda: synced, "admin@lumora.test", base_url="http://x", close_sessions=False)

    async def go() -> Any:
        async with Client(server) as client:
            return await client.list_tools()

    tools = asyncio.run(go()).tools
    assert sorted(t.name for t in tools) == ["get_case", "get_evidence", "list_cases"]
    assert all(
        t.annotations and t.annotations.read_only_hint and not t.annotations.destructive_hint for t in tools
    )


def test_list_and_get_a_case(synced: Session, org: Org) -> None:
    listed = call(synced, "list_cases", {"kind": "risk"}).structured_content
    acme = next(c for c in listed["cases"] if c["account"] == "Acme Corp")
    assert acme["kind"] == "risk" and acme["url"].startswith("http://x/accounts/") and acme["signals"]
    case = call(synced, "get_case", {"case_id": acme["case_id"]}).structured_content
    assert case["account"] == "Acme Corp" and case["explanation"]["claims"]
    assert all(c["evidence_ids"] for c in case["explanation"]["claims"])
    assert case["what_would_lower_risk"]["risk_now"] == case["scores"]["risk"]


def test_evidence_is_masked_and_flagged_text_withheld(synced: Session, org: Org) -> None:
    case = synced.scalars(
        select(AccountCase)
        .join(Account, Account.id == AccountCase.account_id)
        .where(Account.name == "Acme Corp", AccountCase.status == CaseStatus.open)
    ).first()
    assert case is not None
    a = synced.get(Assessment, case.current_assessment_id)
    assert a is not None
    ref = next(e for c in a.claims or [] for e in c["evidence"] if e["kind"] == "activity")
    out = call(
        synced, "get_evidence", {"case_id": str(case.id), "evidence_id": ref["eid"]}
    ).structured_content
    assert out["kind"] == "activity" and "untrusted_customer_text" in out
    act = synced.get(Activity, uuid.UUID(ref["id"]))
    assert act is not None
    act.injection_flag = True
    synced.flush()
    out = call(
        synced, "get_evidence", {"case_id": str(case.id), "evidence_id": ref["eid"]}
    ).structured_content
    assert out["untrusted_customer_text"].startswith("[withheld")
    act.injection_flag = False
    synced.flush()


def test_unknown_user_and_foreign_case_are_refused(synced: Session, org: Org) -> None:
    assert call(synced, "list_cases", {}, user="nobody@lumora.test").is_error
    assert call(synced, "get_case", {"case_id": str(uuid.uuid4())}).is_error
    assert call(synced, "get_evidence", {"case_id": "not-a-uuid", "evidence_id": "E1"}).is_error
