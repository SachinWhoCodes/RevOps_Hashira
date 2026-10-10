"""Synthetic company generator.

Simulates a B2B SaaS vendor ("Lumora") and its customers over a history window, and emits the
data in each source system's own API shape (HubSpot CRM v3 objects with propertiesWithHistory,
Gmail ``users.messages.get`` format=full, Google Calendar events, product usage rows). The same
normalizers therefore process synthetic and real data.

Every account lives through renewal periods. Each period has a *story* that drives behaviour
(usage, replies, meetings, CRM hygiene) and, for past periods, an *outcome*. Stories carry noise
on purpose: some churns are silent surprises, some healthy accounts have false alarms, so no
detector can score perfectly. A ground-truth file records stories, outcomes, true email→account
mapping and true email labels for evaluation (results are labelled "simulated").

Deterministic for a given (seed, today).
"""

from __future__ import annotations

import base64
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from casefile.synth import names
from casefile.synth import templates as T

UTC = ZoneInfo("UTC")

# Past-period outcome → possible stories (weights).
STORIES_FOR_OUTCOME: dict[str, list[tuple[str, float]]] = {
    "churned": [
        ("silent_churn", 0.45),
        ("usage_cliff", 0.2),
        ("champion_departure", 0.15),
        ("surprise_churn", 0.2),
    ],
    "downgraded": [("downgrade_usage", 0.7), ("surprise_churn", 0.3)],
    "late": [("late_hygiene", 0.7), ("healthy", 0.3)],
    "renewed": [("healthy", 0.68), ("false_alarm", 0.15), ("expansion", 0.12), ("new_team", 0.05)],
}
OUTCOME_WEIGHTS = [("renewed", 0.72), ("downgraded", 0.10), ("churned", 0.12), ("late", 0.06)]
CURRENT_STORIES = [
    ("healthy", 0.50),
    ("silent_churn", 0.08),
    ("usage_cliff", 0.05),
    ("champion_departure", 0.05),
    ("downgrade_usage", 0.04),
    ("late_hygiene", 0.05),
    ("false_alarm", 0.07),
    ("expansion", 0.08),
    ("new_team", 0.03),
    ("surprise_churn", 0.05),
]
AT_RISK_STORIES = {"silent_churn", "usage_cliff", "champion_departure", "downgrade_usage"}
SILENT_STORIES = {"silent_churn", "champion_departure"}

SEGMENTS = {
    # seats range, ARR per seat (INR), weekly outbound-thread rate
    "SMB": ((10, 40), (18_000, 26_000), 0.6),
    "Mid-Market": ((40, 150), (16_000, 22_000), 0.9),
    "Enterprise": ((150, 600), (12_000, 18_000), 1.3),
}

DEAL_STAGES = ["appointmentscheduled", "qualifiedtobuy", "contractsent", "closedwon", "closedlost"]


@dataclass
class SynthParams:
    seed: int = 7
    n_accounts: int = 200
    history_days: int = 730
    today: date = field(default_factory=date.today)
    tz: str = "Asia/Kolkata"


@dataclass
class Person:
    first: str
    last: str
    email: str
    title: str
    role: str  # champion | economic_buyer | user
    crm_id: str
    created: datetime
    left_on: date | None = None
    crm_marks_left_on: date | None = None
    personal_email: str | None = None
    in_crm: bool = True

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}"


@dataclass
class Period:
    start: date
    renewal: date
    outcome: str | None  # None = current period, not yet decided
    story: str
    onset: date | None
    deal_id: str
    amount_before: int
    amount_after: int | None = None
    close_on: date | None = None
    usage_end: float = 1.0
    dip_start: date | None = None
    ooo_start: date | None = None
    new_dept_on: date | None = None


@dataclass
class SAccount:
    idx: int
    crm_id: str
    name: str
    domains: list[str]
    segment: str
    seats: int
    owner_idx: int
    contract_start: date
    created: datetime
    base_ratio: float
    depts: list[str]
    contacts: list[Person] = field(default_factory=list)
    periods: list[Period] = field(default_factory=list)
    usage_key: str | None = None
    has_usage: bool = True
    churned_on: date | None = None
    hero: bool = False
    noisy: bool = False
    seat_hist: list[tuple[date, int]] = field(default_factory=list)
    colleagues: list[Person] = field(default_factory=list)

    @property
    def domain(self) -> str:
        return self.domains[0]

    def contact(self, role: str) -> Person:
        return next(c for c in self.contacts if c.role == role)

    def seats_at(self, d: date) -> int:
        seats = self.seat_hist[0][1]
        for when, value in self.seat_hist:
            if when <= d:
                seats = value
        return seats


@dataclass
class Thread:
    gmail_thread_id: str | None
    subject: str
    last_message_id: str | None = None
    references: list[str] = field(default_factory=list)


class Generator:
    def __init__(self, params: SynthParams) -> None:
        self.p = params
        self.rng = random.Random(params.seed)
        self.tz = ZoneInfo(params.tz)
        self.today = params.today
        self.window_start = params.today - timedelta(days=params.history_days)
        self.out: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.truth: dict[str, Any] = {"accounts": {}, "emails": {}, "usage_keys": {}}
        self._counter = 0
        self._crm_counter = 10_000
        self.owners: list[dict[str, Any]] = []

    # ------------------------------------------------------------ small helpers

    def _next(self) -> int:
        self._counter += 1
        return self._counter

    def _crm_id(self) -> str:
        self._crm_counter += 1
        return str(self._crm_counter)

    def _pick(self, weighted: list[tuple[str, float]]) -> str:
        r = self.rng.random() * sum(w for _, w in weighted)
        for value, w in weighted:
            r -= w
            if r <= 0:
                return value
        return weighted[-1][0]

    def _poisson(self, lam: float) -> int:
        limit, k, prod = math.exp(-lam), 0, self.rng.random()
        while prod > limit:
            k += 1
            prod *= self.rng.random()
        return k

    def _at(self, d: date, hour: int | None = None, minute: int | None = None) -> datetime:
        h = hour if hour is not None else self.rng.randint(9, 18)
        m = minute if minute is not None else self.rng.choice([0, 7, 15, 22, 30, 41, 45, 52])
        return datetime.combine(d, time(h, m), tzinfo=self.tz)

    @staticmethod
    def _iso(dt: datetime) -> str:
        return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    @staticmethod
    def _workday(d: date) -> date:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d

    def _owner(self, acc: SAccount) -> dict[str, Any]:
        return self.owners[acc.owner_idx]

    # ------------------------------------------------------------ world building

    def run(self) -> None:
        self._build_owners()
        accounts = self._build_accounts()
        for acc in accounts:
            self._build_periods(acc)
        self._apply_hero(accounts[0])
        for acc in accounts:
            self._build_seat_history(acc)
        for acc in accounts:
            self._emit_company(acc)
            self._emit_contacts(acc)
            self._emit_deals(acc)
            self._simulate_usage(acc)
            self._simulate_meetings(acc)
            self._simulate_email(acc)
            self._record_truth(acc)
        self._simulate_reseller(accounts)
        self._plant_injections(accounts)

    def _build_owners(self) -> None:
        for i, (first, last) in enumerate(names.OWNERS):
            self.owners.append(
                {
                    "id": str(9001 + i),
                    "email": f"{first.lower()}@{names.VENDOR_DOMAIN}",
                    "firstName": first,
                    "lastName": last,
                    "userId": 1001 + i,
                    "archived": False,
                    "createdAt": self._iso(self._at(self.window_start - timedelta(days=400), 10, 0)),
                }
            )
        self.out["hubspot_owners"] = list(self.owners)

    def _company_names(self) -> list[str]:
        combos = [f"{p} {s}" for p in names.COMPANY_PREFIXES for s in names.COMPANY_SUFFIXES]
        self.rng.shuffle(combos)
        return ["Acme Corp", "Globex India", "Globex Europe", *combos]

    def _build_accounts(self) -> list[SAccount]:
        accounts: list[SAccount] = []
        company_names = self._company_names()
        used_emails: set[str] = set()
        for i in range(self.p.n_accounts):
            name = company_names[i]
            slug = name.lower().replace(" ", "-")
            domains = [f"{slug}.test"]
            if name.startswith("Globex"):
                # Two accounts under one parent domain; Europe also has its own subdomain.
                domains = ["eu.globex.test", "globex.test"] if "Europe" in name else ["globex.test"]
            elif self.rng.random() < 0.08:
                domains.append(f"{slug.split('-')[0]}mail.test")
            segment = self._pick([("SMB", 0.45), ("Mid-Market", 0.4), ("Enterprise", 0.15)])
            (lo, hi), _, _ = SEGMENTS[segment]
            seats = self.rng.randint(lo, hi)
            contract_start = self.today - timedelta(days=self.rng.randint(45, 1100))
            acc = SAccount(
                idx=i,
                crm_id=self._crm_id(),
                name=name,
                domains=domains,
                segment=segment,
                seats=seats,
                owner_idx=self.rng.randrange(len(self.owners)),
                contract_start=contract_start,
                created=self._at(contract_start - timedelta(days=self.rng.randint(20, 60))),
                base_ratio=self.rng.uniform(0.55, 0.85),
                depts=self.rng.sample(names.DEPARTMENTS, k=self.rng.randint(1, 3)),
                hero=(i == 0),
                noisy=(segment == "Enterprise" and self.rng.random() < 0.3),
            )
            r = self.rng.random()
            if r < 0.04:
                acc.usage_key = f"ws-{self.rng.randint(10000, 99999)}"  # product id the CRM can't match
            elif r < 0.07:
                acc.has_usage = False
            else:
                acc.usage_key = acc.domain
            if acc.hero:
                acc.usage_key, acc.has_usage, acc.segment = acc.domain, True, "Mid-Market"
                acc.seats, acc.owner_idx = 120, 0
                acc.contract_start = self.today + timedelta(days=43) - timedelta(days=365 * 2)
                acc.created = self._at(acc.contract_start - timedelta(days=30), 11, 0)
            self._build_contacts(acc, used_emails)
            accounts.append(acc)
        return accounts

    def _new_person(self, acc: SAccount, role: str, title: str, used: set[str], created: datetime) -> Person:
        for _ in range(50):
            first, last = self.rng.choice(names.FIRST_NAMES), self.rng.choice(names.LAST_NAMES)
            email = f"{first.lower()}.{last.lower()}@{acc.domain}"
            if email not in used:
                used.add(email)
                break
        personal = None
        if role == "champion" and self.rng.random() < 0.15:
            handle = f"{first.lower()}{last.lower()[0]}{self.rng.randint(10, 99)}"
            personal = f"{handle}@{self.rng.choice(names.PERSONAL_DOMAINS)}"
        return Person(first, last, email, title, role, self._crm_id(), created, personal_email=personal)

    def _build_contacts(self, acc: SAccount, used: set[str]) -> None:
        created = acc.created + timedelta(days=self.rng.randint(1, 20))
        acc.contacts.append(
            self._new_person(acc, "champion", self.rng.choice(names.CHAMPION_TITLES), used, created)
        )
        acc.contacts.append(
            self._new_person(acc, "economic_buyer", self.rng.choice(names.BUYER_TITLES), used, created)
        )
        for _ in range(self.rng.randint(0, 3)):
            acc.contacts.append(
                self._new_person(acc, "user", self.rng.choice(names.USER_TITLES), used, created)
            )

    def _build_periods(self, acc: SAccount) -> None:
        _, (plo, phi), _ = SEGMENTS[acc.segment]
        amount = round(acc.seats * self.rng.uniform(plo, phi), -3)
        start = acc.contract_start
        while True:
            renewal = start + timedelta(days=365)
            current = renewal > self.today
            if current:
                story = self._pick(CURRENT_STORIES)
                outcome = None
            else:
                outcome = self._pick(OUTCOME_WEIGHTS)
                story = self._pick(STORIES_FOR_OUTCOME[outcome])
            period = Period(start, renewal, outcome, story, None, self._crm_id(), int(amount))
            self._shape_period(acc, period)
            acc.periods.append(period)
            if current:
                break
            amount = period.amount_after or amount
            if outcome == "churned":
                acc.churned_on = renewal
                break
            start = renewal

    def _shape_period(self, acc: SAccount, p: Period) -> None:
        rng = self.rng
        if p.story == "silent_churn":
            p.onset, p.usage_end = p.renewal - timedelta(days=rng.randint(70, 140)), rng.uniform(0.4, 0.65)
        elif p.story == "usage_cliff":
            p.onset, p.usage_end = p.renewal - timedelta(days=rng.randint(50, 110)), rng.uniform(0.25, 0.5)
        elif p.story == "champion_departure":
            p.onset, p.usage_end = p.renewal - timedelta(days=rng.randint(60, 150)), rng.uniform(0.6, 0.8)
            champ = acc.contact("champion")
            if champ.left_on is None:
                champ.left_on = p.onset
                if rng.random() < 0.5:
                    champ.crm_marks_left_on = p.onset + timedelta(days=rng.randint(10, 30))
        elif p.story == "downgrade_usage":
            p.onset, p.usage_end = p.renewal - timedelta(days=rng.randint(60, 120)), rng.uniform(0.6, 0.75)
        elif p.story == "false_alarm":
            p.dip_start = p.renewal - timedelta(days=rng.randint(60, 200))
            p.ooo_start = p.dip_start + timedelta(days=rng.randint(0, 10))
        elif p.story == "new_team":
            if p.outcome is None:
                # Current period: the new team started recently, so it is visible as "new" today.
                p.new_dept_on = max(
                    p.start + timedelta(days=30), self.today - timedelta(days=rng.randint(7, 25))
                )
            else:
                p.new_dept_on = p.start + timedelta(days=rng.randint(60, 250))
        # Outcome-dependent close.
        if p.outcome == "late":
            p.close_on = p.renewal + timedelta(days=rng.randint(10, 40))
        elif p.outcome is not None:
            p.close_on = p.renewal + timedelta(days=rng.randint(-5, 3))
        if p.outcome == "downgraded":
            p.amount_after = int(round(p.amount_before * rng.uniform(0.5, 0.8), -3))
        elif p.outcome == "renewed" and p.story in {"expansion", "new_team"}:
            p.amount_after = int(round(p.amount_before * rng.uniform(1.15, 1.4), -3))
        elif p.outcome in {"renewed", "late"}:
            p.amount_after = int(round(p.amount_before * rng.uniform(1.0, 1.08), -3))

    def _apply_hero(self, acme: SAccount) -> None:
        """Acme matches the blueprint's illustrative case: renewal in 43 days, silent champion,
        last meeting 21 days ago and nothing booked, usage down sharply, no next step."""
        current = acme.periods[-1]
        current.story, current.outcome = "silent_churn", None
        current.onset = self.today - timedelta(days=60)
        current.renewal = self.today + timedelta(days=43)
        current.usage_end = 0.6
        champ = acme.contact("champion")
        champ.left_on = champ.crm_marks_left_on = None
        champ.first, champ.last, champ.email = "Dana", "Whitfield", "dana.whitfield@acme-corp.test"
        champ.personal_email = None
        for p in acme.periods[:-1]:
            p.outcome, p.story, p.onset = "renewed", "healthy", None
            p.amount_after = int(round(p.amount_before * 1.05, -3))
            p.close_on = p.renewal - timedelta(days=2)
        for prev, nxt in zip(acme.periods, acme.periods[1:], strict=False):
            nxt.amount_before = prev.amount_after or prev.amount_before

    def _build_seat_history(self, acc: SAccount) -> None:
        seats = acc.seats
        acc.seat_hist = [(acc.contract_start, seats)]
        for p in acc.periods:
            if p.close_on is None or p.amount_after is None:
                continue
            if p.amount_after > p.amount_before * 1.1:
                seats = int(seats * p.amount_after / p.amount_before)
                acc.seat_hist.append((p.close_on, seats))
            elif p.amount_after < p.amount_before * 0.95:
                seats = max(5, int(seats * p.amount_after / p.amount_before))
                acc.seat_hist.append((p.close_on, seats))

    def _period_for(self, acc: SAccount, d: date) -> Period | None:
        for p in acc.periods:
            if p.start <= d < p.renewal or (p is acc.periods[-1] and d >= p.start):
                return p
        return None

    def _active(self, acc: SAccount, d: date) -> bool:
        if d < acc.contract_start:
            return False
        return not (acc.churned_on and d > acc.churned_on)

    def _mood(self, acc: SAccount, d: date) -> str:
        """healthy | declining | silent | expanding | surprise"""
        p = self._period_for(acc, d)
        if p is None:
            return "healthy"
        if p.story in AT_RISK_STORIES and p.onset and d >= p.onset:
            return "silent" if p.story in SILENT_STORIES else "declining"
        if p.story in {"expansion", "new_team"}:
            return "expanding"
        return "healthy"

    # ------------------------------------------------------------ CRM

    def _hist(self, entries: list[tuple[datetime, Any]]) -> list[dict[str, Any]]:
        """HubSpot returns property history newest first."""
        visible = [(t, v) for t, v in entries if t.date() <= self.today]
        return [
            {"value": "" if v is None else str(v), "timestamp": self._iso(t), "sourceType": "CRM_UI"}
            for t, v in sorted(visible, key=lambda e: e[0], reverse=True)
        ]

    def _emit_company(self, acc: SAccount) -> None:
        owner = self._owner(acc)
        seat_hist: list[tuple[datetime, Any]] = [
            (acc.created if i == 0 else self._at(d, 17, 0), n) for i, (d, n) in enumerate(acc.seat_hist)
        ]
        # Contract end date kept on the company; rolled forward when a renewal closes.
        renewal_hist: list[tuple[datetime, Any]] = [(acc.created, acc.periods[0].renewal.isoformat())]
        for prev, nxt in zip(acc.periods, acc.periods[1:], strict=False):
            if prev.close_on:
                renewal_hist.append((self._at(prev.close_on, 17, 5), nxt.renewal.isoformat()))
        props = {
            "name": acc.name,
            "domain": acc.domains[0],
            "hs_additional_domains": ";".join(acc.domains[1:]),
            "hubspot_owner_id": owner["id"],
            "seats_purchased": str(seat_hist[-1][1]),
            "renewal_date": renewal_hist[-1][1],
            "industry": acc.name.split()[-1].upper(),
            "createdate": self._iso(acc.created),
        }
        self.out["hubspot_companies"].append(
            {
                "id": acc.crm_id,
                "properties": props,
                "propertiesWithHistory": {
                    "name": self._hist([(acc.created, acc.name)]),
                    "domain": self._hist([(acc.created, acc.domains[0])]),
                    "hubspot_owner_id": self._hist([(acc.created, owner["id"])]),
                    "seats_purchased": self._hist(seat_hist),
                    "renewal_date": self._hist(renewal_hist),
                },
                "createdAt": self._iso(acc.created),
                "updatedAt": self._iso(self._at(self.today - timedelta(days=1), 9, 0)),
                "archived": False,
            }
        )

    def _emit_contacts(self, acc: SAccount) -> None:
        role_map = {"champion": "CHAMPION", "economic_buyer": "BUDGET_HOLDER", "user": "END_USER"}
        for c in acc.contacts:
            if not c.in_crm or c.created.date() > self.today:
                continue
            status_hist: list[tuple[datetime, Any]] = [(c.created, "active")]
            if c.crm_marks_left_on and c.crm_marks_left_on <= self.today:
                status_hist.append((self._at(c.crm_marks_left_on, 12, 0), "left"))
            props = {
                "email": c.email,
                "firstname": c.first,
                "lastname": c.last,
                "jobtitle": c.title,
                "hs_buying_role": role_map[c.role],
                "employment_status": status_hist[-1][1],
                "associatedcompanyid": acc.crm_id,
                "createdate": self._iso(c.created),
            }
            self.out["hubspot_contacts"].append(
                {
                    "id": c.crm_id,
                    "properties": props,
                    "propertiesWithHistory": {
                        "hs_buying_role": self._hist([(c.created, role_map[c.role])]),
                        "employment_status": self._hist(status_hist),
                        "jobtitle": self._hist([(c.created, c.title)]),
                    },
                    "associations": {
                        "companies": {"results": [{"id": acc.crm_id, "type": "contact_to_company"}]}
                    },
                    "createdAt": self._iso(c.created),
                    "updatedAt": self._iso(status_hist[-1][0]),
                    "archived": False,
                }
            )

    def _emit_deals(self, acc: SAccount) -> None:
        owner = self._owner(acc)
        champ = acc.contact("champion")
        first = acc.periods[0]
        nb_created = self._at(first.start - timedelta(days=self.rng.randint(25, 45)))
        nb_close = self._at(first.start, 16, 0)
        self._deal(
            acc,
            self._crm_id(),
            f"{acc.name} — New business",
            "newbusiness",
            stage_hist=[
                (nb_created, "appointmentscheduled"),
                (nb_close - timedelta(days=12), "contractsent"),
                (nb_close, "closedwon"),
            ],
            amount_hist=[(nb_created, first.amount_before)],
            close_hist=[(nb_created, first.start)],
            next_hist=[(nb_created, "Send order form")],
            created=nb_created,
            owner=owner,
        )
        for p in acc.periods:
            created = self._at(p.renewal - timedelta(days=self.rng.randint(100, 130)))
            if created.date() > self.today:
                continue
            stage: list[tuple[datetime, Any]] = [(created, "appointmentscheduled")]
            stage.append((created + timedelta(days=self.rng.randint(20, 40)), "qualifiedtobuy"))
            amount: list[tuple[datetime, Any]] = [(created, p.amount_before)]
            close: list[tuple[datetime, Any]] = [(created, p.renewal)]
            nexts: list[tuple[datetime, Any]] = []
            at_risk = p.story in AT_RISK_STORIES and p.onset is not None
            if p.story != "late_hygiene":
                t = created + timedelta(days=3)
                end = self._at(p.close_on or self.today, 18, 0)
                while t < end:
                    if at_risk and p.onset and t.date() >= p.onset:
                        break
                    text = self.rng.choice(T.NEXT_STEPS).format(
                        first=champ.first, feature=self.rng.choice(names.FEATURES)
                    )
                    nexts.append((t, text))
                    t += timedelta(days=self.rng.randint(14, 28))
                if at_risk and p.onset and self.rng.random() < 0.5:
                    nexts.append((self._at(p.onset + timedelta(days=self.rng.randint(5, 20))), ""))
            if p.outcome in {"renewed", "late", "downgraded"} and p.close_on:
                stage.append((self._at(p.close_on - timedelta(days=self.rng.randint(8, 20))), "contractsent"))
                stage.append((self._at(p.close_on, 16, 30), "closedwon"))
                if p.outcome == "late":
                    close.append((self._at(p.renewal - timedelta(days=self.rng.randint(1, 6))), p.close_on))
                if p.amount_after and p.amount_after != p.amount_before:
                    amount.append(
                        (self._at(p.close_on - timedelta(days=self.rng.randint(5, 15))), p.amount_after)
                    )
            elif p.outcome == "churned" and p.close_on:
                stage.append((self._at(p.close_on, 16, 30), "closedlost"))
            elif p.outcome is None and not at_risk and (p.renewal - self.today).days < 45:
                stage.append((self._at(self.today - timedelta(days=self.rng.randint(1, 10))), "contractsent"))
            if acc.hero and p is acc.periods[-1]:
                nexts = [
                    (created + timedelta(days=3), "Renewal review call scheduled"),
                    (self._at(self.today - timedelta(days=25), 15, 0), ""),
                ]
                stage = stage[:2]
            self._deal(
                acc,
                p.deal_id,
                f"{acc.name} — Renewal {p.renewal.year}",
                "existingbusiness",
                stage_hist=stage,
                amount_hist=amount,
                close_hist=close,
                next_hist=nexts,
                created=created,
                owner=owner,
            )

    def _deal(
        self,
        acc: SAccount,
        deal_id: str,
        name: str,
        dealtype: str,
        *,
        stage_hist: list,
        amount_hist: list,
        close_hist: list,
        next_hist: list,
        created: datetime,
        owner: dict[str, Any],
    ) -> None:
        def latest(h: list[tuple[datetime, Any]]) -> Any:
            vis = [e for e in h if e[0].date() <= self.today]
            return sorted(vis, key=lambda e: e[0])[-1][1] if vis else None

        close_val = latest(close_hist)
        close_hist_dt = [(t, self._iso(self._at(v, 12, 0))) for t, v in close_hist]
        stage_now = latest(stage_hist)
        props = {
            "dealname": name,
            "dealtype": dealtype,
            "pipeline": "default",
            "dealstage": stage_now,
            "amount": str(latest(amount_hist)),
            "closedate": self._iso(self._at(close_val, 12, 0)) if close_val else None,
            "hs_next_step": latest(next_hist) or "",
            "hubspot_owner_id": owner["id"],
            "createdate": self._iso(created),
            "hs_is_closed": str(stage_now in {"closedwon", "closedlost"}).lower(),
            "hs_is_closed_won": str(stage_now == "closedwon").lower(),
        }
        self.out["hubspot_deals"].append(
            {
                "id": deal_id,
                "properties": props,
                "propertiesWithHistory": {
                    "dealstage": self._hist(stage_hist),
                    "amount": self._hist(amount_hist),
                    "closedate": self._hist(close_hist_dt),
                    "hs_next_step": self._hist(next_hist),
                },
                "associations": {"companies": {"results": [{"id": acc.crm_id, "type": "deal_to_company"}]}},
                "createdAt": self._iso(created),
                "updatedAt": self._iso(
                    max(e[0] for e in stage_hist + next_hist + amount_hist if e[0].date() <= self.today)
                ),
                "archived": False,
            }
        )

    # ------------------------------------------------------------ usage

    def _usage_factor(self, acc: SAccount, d: date) -> float:
        p = self._period_for(acc, d)
        f = 1.0 + 0.03 * math.sin(d.toordinal() / 30)
        if (d.month == 12 and d.day >= 22) or (d.month == 11 and d.day <= 6):
            f *= 0.8  # holiday dip — realistic noise that should not trigger a churn flag alone
        if p is None:
            return f
        if acc.hero and p is acc.periods[-1]:
            if d >= self.today - timedelta(days=28):
                return 0.6 * f
            if d >= self.today - timedelta(days=56):
                return 0.97 * f
            return f
        if p.onset and d >= p.onset and p.story in AT_RISK_STORIES:
            span = max(1, (p.renewal - p.onset).days)
            speed = 3.0 if p.story == "usage_cliff" else 1.0
            frac = min(1.0, speed * (d - p.onset).days / span)
            f *= 1 - frac * (1 - p.usage_end)
        if p.dip_start and p.dip_start <= d < p.dip_start + timedelta(days=21):
            f *= 0.72
        if p.story in {"expansion", "new_team"}:
            span = max(1, (p.renewal - p.start).days)
            f *= 1 + 0.35 * min(1.0, (d - p.start).days / span)
        return f

    def _simulate_usage(self, acc: SAccount) -> None:
        if not acc.has_usage or acc.usage_key is None:
            return
        self.truth["usage_keys"][acc.usage_key] = acc.crm_id
        start = max(self.window_start, acc.contract_start)
        d = start
        while d < self.today:
            if not self._active(acc, d):
                break
            p = self._period_for(acc, d)
            seats = acc.seats_at(d)
            ratio = min(1.0, acc.base_ratio * self._usage_factor(acc, d))
            weekday = 1.0 if d.weekday() < 5 else 0.3
            dau = max(0, round(seats * ratio * weekday * 0.7 * self.rng.uniform(0.9, 1.1)))
            depts = list(acc.depts)
            if p and p.new_dept_on and d >= p.new_dept_on:
                extra = next((x for x in names.DEPARTMENTS if x not in depts), None)
                if extra:
                    depts.append(extra)
            split = {dept: 0 for dept in depts}
            for _ in range(dau):
                split[self.rng.choice(depts)] += 1
            self.out["usage_daily"].append(
                {
                    "account_key": acc.usage_key,
                    "date": d.isoformat(),
                    "active_users": dau,
                    "key_actions": int(dau * self.rng.uniform(8, 14)),
                    "seats_used": min(seats, round(seats * min(1.0, ratio * 1.15))),
                    "departments": split,
                }
            )
            d += timedelta(days=1)

    # ------------------------------------------------------------ calendar

    def _emit_event(
        self,
        acc: SAccount,
        kind: str,
        start: datetime,
        created: datetime,
        attendees: list[Person],
        cancelled_at: datetime | None = None,
    ) -> None:
        owner = self._owner(acc)
        n = self._next()
        summary = T.MEETING_TITLES[kind].format(company=acc.name, product=names.PRODUCT)
        end = start + timedelta(minutes=30 if kind == "checkin" else 60)
        self.out["gcal_events"].append(
            {
                "_casefile": {"calendar": owner["email"]},
                "kind": "calendar#event",
                "id": f"ev{n:08x}",
                "iCalUID": f"ev{n:08x}@google.com",
                "status": "cancelled" if cancelled_at else "confirmed",
                "summary": summary,
                "description": f"Agenda: {self.rng.choice(names.FEATURES)} adoption and open questions.",
                "start": {"dateTime": start.isoformat(), "timeZone": self.p.tz},
                "end": {"dateTime": end.isoformat(), "timeZone": self.p.tz},
                "created": self._iso(created),
                "updated": self._iso(cancelled_at or created),
                "organizer": {"email": owner["email"], "self": True},
                "attendees": [
                    {"email": owner["email"], "organizer": True, "self": True, "responseStatus": "accepted"}
                ]
                + [{"email": a.email, "responseStatus": "accepted"} for a in attendees],
            }
        )

    def _simulate_meetings(self, acc: SAccount) -> None:
        champ, buyer = acc.contact("champion"), acc.contact("economic_buyer")
        d = max(self.window_start, acc.contract_start) + timedelta(days=self.rng.randint(5, 30))
        last_qbr = d
        horizon = self.today + timedelta(days=30)
        booked_future = False
        while d < horizon:
            if not self._active(acc, d):
                break
            day = self._workday(d)
            p = self._period_for(acc, day)
            mood = self._mood(acc, day)
            created = self._at(day - timedelta(days=self.rng.randint(5, 20)))
            if created.date() > self.today:
                break
            stalled = mood in {"silent", "declining"} and (p is not None and p.story != "downgrade_usage")
            if acc.hero and p is acc.periods[-1] and day > self.today - timedelta(days=60):
                stalled = True
            if not stalled:
                kind = "checkin"
                attendees = [c for c in [champ] if not (c.left_on and c.left_on <= day)] or [buyer]
                if (day - last_qbr).days >= 85 and mood != "declining":
                    kind, last_qbr = "qbr", day
                    attendees = [*attendees, buyer]
                elif p and 0 < (p.renewal - day).days <= 50 and p.story not in {"late_hygiene"}:
                    kind = "renewal"
                cancelled = None
                if mood == "declining" and self.rng.random() < 0.4:
                    cancelled = self._at(day - timedelta(days=self.rng.randint(0, 3)))
                start = self._at(day, self.rng.choice([10, 11, 14, 15, 16]), self.rng.choice([0, 30]))
                self._emit_event(acc, kind, start, created, attendees, cancelled)
                if day > self.today:
                    booked_future = True
                elif not cancelled and self.rng.random() < 0.6:
                    self._emit_engagement(acc, "note", start + timedelta(hours=1), champ, buyer)
            d = day + timedelta(days=self.rng.randint(24, 40))
        current = acc.periods[-1]
        if (
            not acc.churned_on
            and not booked_future
            and self._mood(acc, self.today) in {"healthy", "expanding"}
            and not acc.hero
            and current.story != "late_hygiene"
        ):
            start = self._at(self._workday(self.today + timedelta(days=self.rng.randint(3, 25))), 11, 0)
            self._emit_event(
                acc, "checkin", start, self._at(self.today - timedelta(days=self.rng.randint(1, 10))), [champ]
            )
        if acc.hero:
            held = self._at(self.today - timedelta(days=21), 11, 0)
            self._emit_event(acc, "checkin", held, held - timedelta(days=9), [champ])
            self._emit_engagement(acc, "note", held + timedelta(hours=1), champ, buyer)

    def _emit_engagement(
        self, acc: SAccount, kind: str, when: datetime, champ: Person, buyer: Person
    ) -> None:
        owner = self._owner(acc)
        body = self.rng.choice(T.NOTE_BODIES).format(
            first=champ.first,
            buyer_first=buyer.first,
            feature=self.rng.choice(names.FEATURES),
            dept=self.rng.choice(acc.depts),
        )
        props: dict[str, Any] = {
            "hs_timestamp": self._iso(when),
            "hubspot_owner_id": owner["id"],
            "hs_createdate": self._iso(when),
        }
        if kind == "note":
            props["hs_note_body"] = body
        self.out["hubspot_engagements"].append(
            {
                "_object": kind,
                "id": self._crm_id(),
                "properties": props,
                "associations": {
                    "companies": {"results": [{"id": acc.crm_id, "type": f"{kind}_to_company"}]}
                },
                "createdAt": self._iso(when),
                "updatedAt": self._iso(when),
                "archived": False,
            }
        )

    # ------------------------------------------------------------ email

    def _message(
        self,
        acc: SAccount | None,
        *,
        mailbox: str,
        sender: tuple[str, str],
        to: list[str],
        subject: str,
        body: str,
        sent: datetime,
        thread: Thread,
        labels: tuple[str, ...] = (),
        intent: str = "",
        true_account: str | None = None,
        cc: list[str] | None = None,
        headers: dict[str, str] | None = None,
        html: str | None = None,
    ) -> str:
        n = self._next()
        gid = f"{0x18C0000000000000 + n:016x}"
        sender_domain = sender[1].split("@")[1]
        message_id = f"<{n:08x}.{self.p.seed}@mail.{sender_domain}>"
        hdrs = [
            {"name": "From", "value": f"{sender[0]} <{sender[1]}>" if sender[0] else sender[1]},
            {"name": "To", "value": ", ".join(to)},
            {"name": "Subject", "value": subject},
            {"name": "Date", "value": format_datetime(sent)},
            {"name": "Message-ID", "value": message_id},
        ]
        if cc:
            hdrs.append({"name": "Cc", "value": ", ".join(cc)})
        if thread.last_message_id:
            hdrs.append({"name": "In-Reply-To", "value": thread.last_message_id})
            hdrs.append({"name": "References", "value": " ".join(thread.references)})
        for k, v in (headers or {}).items():
            hdrs.append({"name": k, "value": v})
        b64 = base64.urlsafe_b64encode(body.encode()).decode()
        payload: dict[str, Any]
        if html:
            hb64 = base64.urlsafe_b64encode(html.encode()).decode()
            payload = {
                "mimeType": "multipart/alternative",
                "headers": hdrs,
                "body": {"size": 0},
                "parts": [
                    {"partId": "0", "mimeType": "text/plain", "body": {"size": len(body), "data": b64}},
                    {"partId": "1", "mimeType": "text/html", "body": {"size": len(html), "data": hb64}},
                ],
            }
        else:
            payload = {"mimeType": "text/plain", "headers": hdrs, "body": {"size": len(body), "data": b64}}
        if thread.gmail_thread_id is None:
            thread.gmail_thread_id = gid
        outbound = sender[1].endswith("@" + names.VENDOR_DOMAIN)
        msg = {
            "_casefile": {"mailbox": mailbox},
            "id": gid,
            "threadId": thread.gmail_thread_id,
            "labelIds": ["SENT"] if outbound else ["INBOX"],
            "snippet": " ".join(body.split())[:160],
            "internalDate": str(int(sent.timestamp() * 1000)),
            "sizeEstimate": len(body) + 400,
            "payload": payload,
        }
        self.out["gmail_messages"].append(msg)
        thread.last_message_id = message_id
        thread.references.append(message_id)
        self.truth["emails"][message_id] = {
            "account": true_account if true_account is not None else (acc.crm_id if acc else None),
            "labels": list(labels),
            "intent": intent,
            "injection": "injection" in labels,
        }
        if cc and any(c.endswith("@" + names.VENDOR_DOMAIN) for c in cc):
            # The same message also lands in a cc'd colleague's mailbox (tests Message-ID dedupe).
            dup = json.loads(json.dumps(msg))
            dup["id"] = f"{0x19C0000000000000 + n:016x}"
            dup["threadId"] = dup["id"]
            dup["_casefile"]["mailbox"] = next(c for c in cc if c.endswith("@" + names.VENDOR_DOMAIN))
            dup["labelIds"] = ["INBOX"]
            self.out["gmail_messages"].append(dup)
        return message_id

    def _fmt(
        self, acc: SAccount, person: Person, extra: dict[str, Any] | None = None, on: date | None = None
    ) -> dict[str, Any]:
        owner = self._owner(acc)
        p = self._period_for(acc, on or self.today) or acc.periods[-1]
        vals = {
            "first": person.first,
            "owner_first": owner["firstName"],
            "company": acc.name,
            "product": names.PRODUCT,
            "feature": self.rng.choice(names.FEATURES),
            "competitor": self.rng.choice(names.COMPETITORS),
            "dept": self.rng.choice(names.DEPARTMENTS),
            "n_seats": self.rng.choice([10, 15, 20, 25]),
            "buyer_first": acc.contact("economic_buyer").first,
            "renewal_date": p.renewal.strftime("%d %b %Y"),
            "prev_subject": "",
        }
        vals.update(extra or {})
        return vals

    def _reply_prob(self, acc: SAccount, d: date) -> float:
        mood = self._mood(acc, d)
        p = self._period_for(acc, d)
        if mood == "silent":
            return 0.12
        if mood == "declining":
            return 0.45 if p and p.story == "usage_cliff" else 0.55
        return 0.85

    def _reply_intent(self, acc: SAccount, d: date) -> str:
        mood = self._mood(acc, d)
        p = self._period_for(acc, d)
        near = p is not None and (p.renewal - d).days <= 120
        if mood in {"declining", "silent"}:
            choices = [("complaint", 0.25), ("frustration", 0.1), ("budget", 0.15), ("positive", 0.35)]
            if near:
                choices.append(("competitor", 0.15))
            return self._pick(choices)
        if mood == "expanding":
            return self._pick([("seat_request", 0.2), ("pricing_question", 0.15), ("positive", 0.65)])
        if acc.noisy:
            return self._pick([("complaint", 0.12), ("positive", 0.6), ("scheduling", 0.28)])
        if p and p.story == "surprise_churn" and p.outcome and (p.renewal - d).days < 21:
            return "budget"
        return self._pick([("positive", 0.6), ("scheduling", 0.3), ("feature_question", 0.1)])

    def _send_thread(self, acc: SAccount, day: date, intent: str) -> None:
        owner = self._owner(acc)
        mailbox = owner["email"]
        recipient = acc.contact("champion")
        if self.rng.random() < 0.2:
            recipient = self.rng.choice(acc.contacts)
        tpl = self.rng.choice(T.OUTBOUND[intent])
        vals = self._fmt(acc, recipient, on=day)
        subject = tpl.subject.format(**vals)
        thread = Thread(None, subject)
        sent = self._at(day)
        cc = [self.rng.choice(self.owners)["email"]] if self.rng.random() < 0.04 else None
        if cc and cc[0] == mailbox:
            cc = None
        self._message(
            acc,
            mailbox=mailbox,
            sender=(f"{owner['firstName']} {owner['lastName']}", mailbox),
            to=[recipient.email],
            subject=subject,
            body=tpl.body.format(**vals),
            sent=sent,
            thread=thread,
            intent=intent,
            cc=cc,
        )
        self._replies(acc, recipient, thread, sent, mailbox)

    def _replies(
        self, acc: SAccount, recipient: Person, thread: Thread, sent: datetime, mailbox: str
    ) -> None:
        owner = self._owner(acc)
        p = self._period_for(acc, sent.date())
        followups = 0
        while True:
            d = sent.date()
            if recipient.left_on and d >= recipient.left_on:
                successor = next((c for c in acc.contacts if c.role == "economic_buyer"), recipient)
                at = sent + timedelta(minutes=self.rng.randint(1, 20))
                if self.rng.random() < 0.5:
                    vals = self._fmt(
                        acc,
                        recipient,
                        {
                            "prev_subject": thread.subject,
                            "successor_name": successor.name,
                            "successor_email": successor.email,
                        },
                    )
                    self._message(
                        acc,
                        mailbox=mailbox,
                        sender=(recipient.name, recipient.email),
                        to=[mailbox],
                        subject=T.LEFT_COMPANY.subject.format(**vals),
                        body=T.LEFT_COMPANY.body.format(**vals),
                        sent=at,
                        thread=thread,
                        labels=T.LEFT_COMPANY.labels,
                        intent="left_company",
                        headers={"Auto-Submitted": "auto-replied"},
                    )
                else:
                    vals = {"failed_email": recipient.email}
                    self._message(
                        acc,
                        mailbox=mailbox,
                        sender=("Mail Delivery Subsystem", "mailer-daemon@googlemail.com"),
                        to=[mailbox],
                        subject=T.BOUNCE.subject,
                        body=T.BOUNCE.body.format(**vals),
                        sent=at,
                        thread=thread,
                        labels=T.BOUNCE.labels,
                        intent="bounce",
                        headers={"X-Failed-Recipients": recipient.email, "Auto-Submitted": "auto-replied"},
                    )
                return
            if (
                p
                and p.ooo_start
                and p.ooo_start <= d < p.ooo_start + timedelta(days=14)
                and recipient.role == "champion"
            ):
                vals = self._fmt(
                    acc,
                    recipient,
                    {
                        "prev_subject": thread.subject,
                        "return_date": (p.ooo_start + timedelta(days=14)).strftime("%d %b"),
                    },
                )
                self._message(
                    acc,
                    mailbox=mailbox,
                    sender=(recipient.name, recipient.email),
                    to=[mailbox],
                    subject=T.OUT_OF_OFFICE.subject.format(**vals),
                    body=T.OUT_OF_OFFICE.body.format(**vals),
                    sent=sent + timedelta(minutes=2),
                    thread=thread,
                    labels=T.OUT_OF_OFFICE.labels,
                    intent="out_of_office",
                    headers={"Auto-Submitted": "auto-replied"},
                )
                return
            if self.rng.random() < self._reply_prob(acc, d):
                at = sent + timedelta(hours=self.rng.randint(2, 70))
                if at.date() >= self.today or not self._active(acc, at.date()):
                    return
                intent = self._reply_intent(acc, at.date())
                tpl = self.rng.choice(T.INBOUND[intent])
                vals = self._fmt(acc, recipient, {"prev_subject": thread.subject})
                from_addr = recipient.email
                if recipient.personal_email and self.rng.random() < 0.3:
                    from_addr = recipient.personal_email
                reply_cc = [self._colleague(acc).email] if self.rng.random() < 0.08 else None
                self._message(
                    acc,
                    mailbox=mailbox,
                    sender=(recipient.name, from_addr),
                    to=[mailbox],
                    cc=reply_cc,
                    subject=tpl.subject.format(**vals),
                    body=tpl.body.format(**vals),
                    sent=at,
                    thread=thread,
                    labels=tpl.labels,
                    intent=intent,
                )
                return
            if followups >= 2:
                return
            followups += 1
            sent = self._at(self._workday(d + timedelta(days=self.rng.randint(5, 9))))
            if sent.date() >= self.today or not self._active(acc, sent.date()):
                return
            tpl = self.rng.choice(T.OUTBOUND["followup"])
            vals = self._fmt(acc, recipient, {"prev_subject": thread.subject})
            self._message(
                acc,
                mailbox=mailbox,
                sender=(f"{owner['firstName']} {owner['lastName']}", mailbox),
                to=[recipient.email],
                subject=tpl.subject.format(**vals),
                body=tpl.body.format(**vals),
                sent=sent,
                thread=thread,
                intent="followup",
            )

    def _simulate_email(self, acc: SAccount) -> None:
        _, _, rate = SEGMENTS[acc.segment]
        if acc.noisy:
            rate *= 2.5
        week = max(self.window_start, acc.contract_start)
        week -= timedelta(days=week.weekday())
        while week < self.today:
            p = self._period_for(acc, week)
            if not self._active(acc, week) or p is None:
                week += timedelta(days=7)
                if acc.churned_on and week > acc.churned_on:
                    break
                continue
            hero_quiet = acc.hero and week >= self.today - timedelta(days=30)
            for _ in range(0 if hero_quiet else self._poisson(rate)):
                day = week + timedelta(days=self.rng.randint(0, 4))
                if day >= self.today:
                    continue
                to_renewal = (p.renewal - day).days
                if 0 < to_renewal <= 90 and self.rng.random() < 0.35:
                    intent = "renewal_prep"
                else:
                    intent = self._pick([("checkin", 0.55), ("product_update", 0.25), ("qbr_invite", 0.2)])
                self._send_thread(acc, day, intent)
            mood = self._mood(acc, week)
            inbound_p = {"declining": 0.18, "expanding": 0.15}.get(mood, 0.06)
            if not hero_quiet and self.rng.random() < inbound_p:
                day = week + timedelta(days=self.rng.randint(0, 4))
                if day < self.today:
                    intent = {
                        "declining": "complaint",
                        "expanding": self.rng.choice(["seat_request", "pricing_question"]),
                    }.get(mood, "feature_question")
                    self._inbound_thread(acc, day, intent)
            week += timedelta(days=7)
        if acc.hero:
            self._hero_emails(acc)

    def _colleague(self, acc: SAccount) -> Person:
        """Someone at the customer the CRM does not know about (identity-resolution noise).
        Each account has a small, stable pool of such people."""
        if not acc.colleagues:
            for _ in range(self.rng.randint(1, 3)):
                first, last = self.rng.choice(names.FIRST_NAMES), self.rng.choice(names.LAST_NAMES)
                email = f"{first.lower()}.{last.lower()}@{self.rng.choice(acc.domains)}"
                acc.colleagues.append(Person(first, last, email, "", "user", "", acc.created, in_crm=False))
        return self.rng.choice(acc.colleagues)

    def _inbound_thread(self, acc: SAccount, day: date, intent: str) -> None:
        owner = self._owner(acc)
        person = self.rng.choice(
            [c for c in acc.contacts if not (c.left_on and c.left_on <= day)] or acc.contacts
        )
        if self.rng.random() < 0.15:
            person = self._colleague(acc)
        tpl = next((t for t in T.INBOUND[intent] if "{prev_subject}" not in t.subject), T.INBOUND[intent][0])
        vals = self._fmt(acc, person, {"prev_subject": "Follow-up"}, on=day)
        subject = tpl.subject.format(**vals)
        thread = Thread(None, subject)
        self._message(
            acc,
            mailbox=owner["email"],
            sender=(person.name, person.email),
            to=[owner["email"]],
            subject=subject,
            body=tpl.body.format(**vals),
            sent=self._at(day),
            thread=thread,
            labels=tpl.labels,
            intent=intent,
        )

    def _hero_emails(self, acc: SAccount) -> None:
        owner = self._owner(acc)
        mailbox = owner["email"]
        champ = acc.contact("champion")
        sender = (f"{owner['firstName']} {owner['lastName']}", mailbox)
        # Five weeks ago: a normal exchange — the silence is new.
        vals = self._fmt(acc, champ)
        t = Thread(None, f"Checking in — {acc.name}")
        sent = self._at(self.today - timedelta(days=38), 11, 5)
        self._message(
            acc,
            mailbox=mailbox,
            sender=sender,
            to=[champ.email],
            subject=t.subject,
            body=T.OUTBOUND["checkin"][0].body.format(**vals),
            sent=sent,
            thread=t,
            intent="checkin",
        )
        self._message(
            acc,
            mailbox=mailbox,
            sender=(champ.name, champ.email),
            to=[mailbox],
            subject=f"Re: {t.subject}",
            body=T.INBOUND["positive"][1].body.format(**self._fmt(acc, champ)),
            sent=sent + timedelta(hours=20),
            thread=t,
            intent="positive",
        )
        # 16 and 9 days ago: renewal outreach, unanswered.
        t2 = Thread(None, f"{acc.name} renewal — next steps")
        self._message(
            acc,
            mailbox=mailbox,
            sender=sender,
            to=[champ.email],
            subject=t2.subject,
            body=T.OUTBOUND["renewal_prep"][0].body.format(**vals),
            sent=self._at(self.today - timedelta(days=16), 10, 30),
            thread=t2,
            intent="renewal_prep",
        )
        self._message(
            acc,
            mailbox=mailbox,
            sender=sender,
            to=[champ.email],
            subject=f"Re: {t2.subject}",
            body=T.OUTBOUND["followup"][0].body.format(**vals),
            sent=self._at(self.today - timedelta(days=9), 16, 10),
            thread=t2,
            intent="followup",
        )

    def _simulate_reseller(self, accounts: list[SAccount]) -> None:
        """A reseller writes about three customers from its own domain: unmatchable by domain."""
        _, rdomain = names.RESELLER
        for acc in accounts[10:13]:
            if acc.churned_on:
                continue
            owner = self._owner(acc)
            day = self._workday(self.today - timedelta(days=self.rng.randint(5, 60)))
            vals = self._fmt(acc, acc.contact("champion"), {"first": "Meena"})
            t = Thread(None, T.RESELLER.subject.format(**vals))
            self._message(
                None,
                mailbox=owner["email"],
                sender=("Meena Joshi", f"meena@{rdomain}"),
                to=[owner["email"]],
                subject=t.subject,
                body=T.RESELLER.body.format(**vals),
                sent=self._at(day),
                thread=t,
                intent="reseller",
                true_account=acc.crm_id,
            )

    def _plant_injections(self, accounts: list[SAccount]) -> None:
        live = [a for a in accounts[3:] if not a.churned_on]
        for i, acc in enumerate(self.rng.sample(live, k=min(5, len(live)))):
            owner = self._owner(acc)
            person = acc.contact("champion")
            if person.left_on:
                person = acc.contact("economic_buyer")
            day = self._workday(self.today - timedelta(days=self.rng.randint(3, 40)))
            vals = self._fmt(acc, person, {"prev_subject": "Quick question"})
            if i == 0:
                body = f"Hi {vals['owner_first']},\n\nThanks for the update, all good.\n\n{person.first}"
                self._message(
                    acc,
                    mailbox=owner["email"],
                    sender=(person.name, person.email),
                    to=[owner["email"]],
                    subject="Re: Quick question",
                    body=body,
                    sent=self._at(day),
                    thread=Thread(None, "Quick question"),
                    labels=("injection",),
                    intent="injection_hidden",
                    html=T.HIDDEN_INJECTION_HTML.format(**vals),
                )
            else:
                tpl = T.INBOUND["injection"][i % 2]
                self._message(
                    acc,
                    mailbox=owner["email"],
                    sender=(person.name, person.email),
                    to=[owner["email"]],
                    subject=tpl.subject.format(**vals),
                    body=tpl.body.format(**vals),
                    sent=self._at(day),
                    thread=Thread(None, tpl.subject.format(**vals)),
                    labels=tpl.labels,
                    intent="injection",
                )

    # ------------------------------------------------------------ ground truth

    def _record_truth(self, acc: SAccount) -> None:
        self.truth["accounts"][acc.crm_id] = {
            "name": acc.name,
            "domains": acc.domains,
            "segment": acc.segment,
            "owner_email": self._owner(acc)["email"],
            "usage_key": acc.usage_key,
            "has_usage": acc.has_usage,
            "hero": acc.hero,
            "noisy": acc.noisy,
            "churned_on": acc.churned_on.isoformat() if acc.churned_on else None,
            "current_story": None if acc.churned_on else acc.periods[-1].story,
            "current_at_risk": (not acc.churned_on) and acc.periods[-1].story in AT_RISK_STORIES,
            "periods": [
                {
                    "renewal": p.renewal.isoformat(),
                    "outcome": p.outcome,
                    "story": p.story,
                    "onset": p.onset.isoformat() if p.onset else None,
                    "deal_id": p.deal_id,
                }
                for p in acc.periods
            ],
        }

    # ------------------------------------------------------------ output

    def write(self, out_dir: Path) -> dict[str, Any]:
        out_dir.mkdir(parents=True, exist_ok=True)
        counts: dict[str, int] = {}
        for stream, rows in sorted(self.out.items()):
            if stream == "hubspot_engagements":
                rows = sorted(rows, key=lambda r: r["properties"]["hs_timestamp"])
            if stream == "gmail_messages":
                rows = sorted(rows, key=lambda r: int(r["internalDate"]))
            with (out_dir / f"{stream}.jsonl").open("w") as fh:
                for row in rows:
                    fh.write(json.dumps(row, separators=(",", ":")) + "\n")
            counts[stream] = len(rows)
        manifest = {
            "generator": "casefile.synth",
            "seed": self.p.seed,
            "n_accounts": self.p.n_accounts,
            "history_days": self.p.history_days,
            "today": self.today.isoformat(),
            "timezone": self.p.tz,
            "vendor": {"name": names.VENDOR_NAME, "domain": names.VENDOR_DOMAIN},
            "counts": counts,
            "label": "simulated",
        }
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        truth = {"today": self.today.isoformat(), "vendor_domain": names.VENDOR_DOMAIN, **self.truth}
        (out_dir / "ground_truth.json").write_text(json.dumps(truth, indent=1))
        return manifest


def generate(params: SynthParams, out_dir: Path) -> dict[str, Any]:
    gen = Generator(params)
    gen.run()
    return gen.write(out_dir)
