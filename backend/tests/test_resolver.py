"""Identity rules on hand-built cases: free-mail, subdomains, shared domains, threads, meetings."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from casefile.bootstrap import ensure_org
from casefile.db.models import Account, ActivityType, Contact, ContactRole, IdentityOverride, MatchMethod
from casefile.identity.resolver import IdentityIndex


@pytest.fixture
def index(db: Session) -> tuple[IdentityIndex, dict[str, uuid.UUID]]:
    org = ensure_org(
        db, slug="resolver-test", name="Vendor", timezone="UTC", internal_domains=["vendor.test"]
    )
    ids: dict[str, uuid.UUID] = {}
    for crm_id, name, domains in [
        ("1", "Acme", ["acme.test"]),
        ("2", "Globex India", ["globex.test"]),
        ("3", "Globex Europe", ["eu.globex.test", "globex.test"]),
        ("4", "Initech", ["initech.test"]),
    ]:
        acc = Account(org_id=org.id, crm_source="hubspot", crm_id=crm_id, name=name, domains=domains)
        db.add(acc)
        db.flush()
        ids[name] = acc.id
    db.add_all(
        [
            Contact(org_id=org.id, account_id=ids["Acme"], email="dana@acme.test", role=ContactRole.champion),
            Contact(org_id=org.id, account_id=ids["Globex India"], email="ravi@globex.test"),
            Contact(org_id=org.id, account_id=ids["Initech"], email="pat.personal@gmail.com"),
            IdentityOverride(org_id=org.id, kind="domain", value="partner.test", account_id=ids["Initech"]),
        ]
    )
    db.flush()
    return IdentityIndex(db, org), ids


def test_exact_contact_email_wins(index: tuple[IdentityIndex, dict[str, uuid.UUID]]) -> None:
    idx, ids = index
    res = idx.resolve(["dana@acme.test", "me@vendor.test"], kind=ActivityType.email)
    assert (res.account_id, res.method, res.confidence) == (ids["Acme"], MatchMethod.contact_email, 1.0)


def test_known_contact_on_a_shared_domain_is_not_ambiguous(
    index: tuple[IdentityIndex, dict[str, uuid.UUID]],
) -> None:
    idx, ids = index
    assert idx.resolve(["ravi@globex.test"], kind=ActivityType.email).account_id == ids["Globex India"]


def test_unknown_person_on_a_shared_domain_is_ambiguous(
    index: tuple[IdentityIndex, dict[str, uuid.UUID]],
) -> None:
    idx, ids = index
    res = idx.resolve(["new.person@globex.test"], kind=ActivityType.email)
    assert res.account_id is None and res.method == MatchMethod.ambiguous
    assert set(res.candidates) == {ids["Globex India"], ids["Globex Europe"]}


def test_subdomain_maps_to_its_own_account(index: tuple[IdentityIndex, dict[str, uuid.UUID]]) -> None:
    idx, ids = index
    res = idx.resolve(["someone@eu.globex.test"], kind=ActivityType.email)
    assert (res.account_id, res.method) == (ids["Globex Europe"], MatchMethod.domain)


def test_free_mail_never_matches_by_domain(index: tuple[IdentityIndex, dict[str, uuid.UUID]]) -> None:
    idx, _ = index
    assert idx.resolve(["stranger@gmail.com"], kind=ActivityType.email).method == MatchMethod.unmatched


def test_free_mail_known_contact_still_matches(index: tuple[IdentityIndex, dict[str, uuid.UUID]]) -> None:
    idx, ids = index
    assert idx.resolve(["pat.personal@gmail.com"], kind=ActivityType.email).account_id == ids["Initech"]


def test_thread_inheritance_for_personal_address(index: tuple[IdentityIndex, dict[str, uuid.UUID]]) -> None:
    idx, ids = index
    res = idx.resolve(["dana.home@gmail.com"], kind=ActivityType.email, thread_account=ids["Acme"])
    assert (res.account_id, res.method, res.confidence) == (ids["Acme"], MatchMethod.thread, 0.95)


def test_internal_only_is_never_a_customer(index: tuple[IdentityIndex, dict[str, uuid.UUID]]) -> None:
    idx, _ = index
    res = idx.resolve(["a@vendor.test", "b@vendor.test"], kind=ActivityType.email)
    assert (res.account_id, res.method) == (None, MatchMethod.internal_only)


def test_system_addresses_are_ignored(index: tuple[IdentityIndex, dict[str, uuid.UUID]]) -> None:
    idx, ids = index
    res = idx.resolve(["mailer-daemon@googlemail.com", "dana@acme.test"], kind=ActivityType.email)
    assert res.account_id == ids["Acme"]


def test_domain_override(index: tuple[IdentityIndex, dict[str, uuid.UUID]]) -> None:
    idx, ids = index
    res = idx.resolve(["meena@partner.test"], kind=ActivityType.email)
    assert (res.account_id, res.method) == (ids["Initech"], MatchMethod.domain)


def test_meeting_majority(index: tuple[IdentityIndex, dict[str, uuid.UUID]]) -> None:
    idx, ids = index
    res = idx.resolve(["x@acme.test", "y@acme.test", "z@initech.test"], kind=ActivityType.meeting)
    assert (res.account_id, res.method, res.confidence) == (ids["Acme"], MatchMethod.meeting_majority, 0.8)


def test_email_with_two_customers_is_ambiguous(index: tuple[IdentityIndex, dict[str, uuid.UUID]]) -> None:
    idx, _ = index
    res = idx.resolve(["x@acme.test", "z@initech.test"], kind=ActivityType.email)
    assert res.method == MatchMethod.ambiguous


def test_usage_keys(index: tuple[IdentityIndex, dict[str, uuid.UUID]]) -> None:
    idx, ids = index
    assert idx.resolve_usage_key("acme.test") == ids["Acme"]
    assert idx.resolve_usage_key("1") == ids["Acme"]  # CRM id
    assert idx.resolve_usage_key("globex.test") is None  # shared domain: needs a person
    assert idx.resolve_usage_key("ws-12345") is None
