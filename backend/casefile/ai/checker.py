"""Claim checker — plain code that decides whether an AI-written sentence may be shown (rule R2).

A claim passes only if all of these hold:
1. it cites at least one evidence ID, and every cited ID exists in the evidence pack;
2. every cited fact key exists in the facts table;
3. every number, percentage, spelled-out number and date in the text matches a value in the facts it
   cites, in the facts of the signals behind the evidence it cites, or in the cited evidence itself
   (allowed: dropping a minus sign, rounding to a whole number or one decimal);
4. every proper noun appears somewhere in the evidence pack (no invented people or competitors);
5. it mentions no money concessions or promises (discounts, refunds, credits, guarantees).

The headline and blind-spot note are checked with rules 3-5 against the whole pack.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from casefile.ai.evidence import EvidencePack

MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}
MONTH = (
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?"
    r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{MONTH}\.?(?:,?\s+(\d{{4}}))?\b")
MDY = re.compile(rf"\b{MONTH}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b")
# A number, optionally followed by a unit we check separately: a percentage must match a percentage
# fact and "N days" a days fact, so a real number cannot be reused with a different meaning.
NUMBER = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{2,3})+|\d+)(?:\.(\d+))?(\s*(?:%|percent\b|per cent\b)|\s+days?\b)?", re.I
)
WORD_NUMBERS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
}
WORD_NUMBER = re.compile(r"\b(" + "|".join(WORD_NUMBERS) + r")\b(\s+days?\b)?", re.I)
MONEY = re.compile(
    r"\b(discounts?|refunds?|credit notes?|service credits?|waive[ds]?|free (?:months?|seats?|upgrade)|"
    r"price (?:cut|reduction|match)|special (?:offer|pricing|price)|lower (?:price|rate)|guarantee[sd]?)\b"
    r"|\d+\s?% off",
    re.I,
)
PROPER = re.compile(r"\b[A-Z][a-zA-Z]+(?:'s)?\b")

# Ordinary words that may be capitalised (sentence starts, domain words). Anything else capitalised
# must appear in the evidence pack.
COMMON = set(
    """
a about above across active actions activity after again against all also although an and another
any are as at average back based be because been before behind being below between both business but
by calendar call calls can cancelled case champion check checked checks cleared contact contacts
could crm customer customers daily data date day days deal decline declined declining down during
each early earlier economic email emails engagement even every evidence exec executive expansion
fell few first for from further had has have he her his however if in increase increased interest
into is it its key last late later latest least less likely low many meeting meetings might more
most much negative never new next no none not nothing now of on once only open opportunity or other
our out over owner part past people per positive possible product purchased quarter quarterly recent
recently recorded renewal renews reply replied replies requests review risk rose saturation score
seat seats send sent she signal signals silence silent since slow so some stage step still such team
teams than that the their them then there these they this those three through to today total two
under unanswered until up usage use used users very was we week weeks were what when where whether
which while who will with within without would year years yet buyer buyers check-in monthly booked
agenda adoption questions emailed asked asking upgrade pricing complaint complaints frustration
competitor competitors mention mentions mentioned tone notes note task tasks call
""".split()
)
ACRONYM = re.compile(r"^[A-Z]{2,6}s?$")
WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}


@dataclass
class Allowed:
    numbers: set[float] = field(default_factory=set)  # any number, unitless use
    pct: set[float] = field(default_factory=set)  # may be written as a percentage
    days: set[float] = field(default_factory=set)  # may be written as "N days"
    dates: set[date] = field(default_factory=set)

    def add(self, value: float, unit: str | None) -> None:
        values = {value, abs(value)}
        self.numbers.update(values)
        if unit == "pct":
            self.pct.update(values)
        elif unit == "days":
            self.days.update(values)


def _to_date(day: str, month: str, year: str | None) -> tuple[int | None, int, int] | None:
    try:
        d, m = int(day), MONTHS[month[:3].lower()]
        date(int(year) if year else 2000, m, d)  # validates day/month (2000 is a leap year)
        return (int(year) if year else None, m, d)
    except (KeyError, ValueError):
        return None


def extract_dates(text: str) -> tuple[list[tuple[int | None, int, int] | None], str]:
    """Dates found in the text (None for an impossible date like 31 Sep) and the text without them."""
    found: list[tuple[int | None, int, int] | None] = []

    def iso(m: re.Match[str]) -> str:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            found.append((d.year, d.month, d.day))
        except ValueError:
            found.append(None)
        return " "

    def dmy(m: re.Match[str]) -> str:
        found.append(_to_date(m.group(1), m.group(2), m.group(3)))
        return " "

    def mdy(m: re.Match[str]) -> str:
        found.append(_to_date(m.group(2), m.group(1), m.group(3)))
        return " "

    text = ISO_DATE.sub(iso, text)
    text = DMY.sub(dmy, text)
    text = MDY.sub(mdy, text)
    return found, text


def _unit(suffix: str | None) -> str | None:
    if not suffix:
        return None
    return "days" if "day" in suffix.lower() else "pct"


def extract_numbers(text: str) -> list[tuple[float, str | None]]:
    nums = []
    for m in NUMBER.finditer(text):
        whole = m.group(1).replace(",", "")
        nums.append((float(f"{whole}.{m.group(2)}" if m.group(2) else whole), _unit(m.group(3))))
    nums += [(float(WORD_NUMBERS[w.lower()]), _unit(suffix)) for w, suffix in WORD_NUMBER.findall(text)]
    return nums


def collect(value: Any, allowed: Allowed, key: str = "") -> None:
    """Add the numbers and dates in a fact (or evidence text) to the allowed sets. A fact's key decides
    its unit: keys containing "pct" are percentages, keys containing "days" are day counts."""
    if isinstance(value, bool) or value is None:
        return
    unit = "pct" if "pct" in key else "days" if "days" in key else None
    if isinstance(value, int | float):
        allowed.add(float(value), unit)
    elif isinstance(value, str):
        dates, rest = extract_dates(value)
        for d in dates:
            if d is not None and d[0] is not None:
                allowed.dates.add(date(d[0], d[1], d[2]))
                allowed.numbers.add(float(d[0]))
        for n, text_unit in extract_numbers(rest):
            allowed.add(n, unit or text_unit)
    elif isinstance(value, list | tuple | set):
        for v in value:
            collect(v, allowed, key)
    elif isinstance(value, dict):
        for k, v in value.items():
            collect(v, allowed, str(k))


def collect_evidence(item: Any, allowed: Allowed) -> None:
    # Evidence timestamps count as dates only: "…T11:00:00+05:30" must not make 11 or 5 citable numbers.
    collect([(item.date or "")[:10], item.label, item.text], allowed)


def _number_ok(n: float, unit: str | None, allowed: Allowed) -> bool:
    pool = allowed.pct if unit == "pct" else allowed.days if unit == "days" else allowed.numbers
    return any(abs(n - a) < 1e-9 or abs(n - round(a)) < 1e-9 or abs(n - round(a, 1)) < 1e-9 for a in pool)


def _date_ok(d: tuple[int | None, int, int], allowed: set[date]) -> bool:
    year, month, day = d
    return any(a.month == month and a.day == day and (year is None or a.year == year) for a in allowed)


def _content_reasons(text: str, allowed: Allowed, pack: EvidencePack) -> list[str]:
    reasons: list[str] = []
    dates, rest = extract_dates(text)
    for d in dates:
        if d is None:
            reasons.append("impossible date")
        elif not _date_ok(d, allowed.dates):
            reasons.append(
                f"date {d[2]:02d}/{d[1]:02d}{'/' + str(d[0]) if d[0] else ''} not in the cited evidence"
            )
    for n, unit in extract_numbers(rest):
        if not _number_ok(n, unit, allowed):
            shown = f"{n:g}%" if unit == "pct" else f"{n:g} days" if unit == "days" else f"{n:g}"
            reasons.append(f"number {shown} not in the cited facts")
    corpus_words = set(PROPER.findall(pack.corpus))
    for word in PROPER.findall(text):
        base = word[:-2] if word.endswith("'s") else word
        low = base.lower()
        if (
            low in COMMON
            or low[:3] in MONTHS
            or low in WEEKDAYS
            or ACRONYM.match(base)
            or base in corpus_words
            or word in corpus_words
        ):
            continue
        reasons.append(f"name or term '{base}' does not appear in the evidence")
    if m := MONEY.search(text):
        reasons.append(f"mentions '{m.group(0)}' (money and promises stay with people)")
    return reasons


@dataclass
class ClaimVerdict:
    text: str
    evidence_ids: list[str]
    fact_keys: list[str]
    ok: bool
    reasons: list[str]


def check_claim(text: str, evidence_ids: list[str], fact_keys: list[str], pack: EvidencePack) -> ClaimVerdict:
    reasons: list[str] = []
    if not text.strip():
        reasons.append("empty claim")
    if len(text) > 400:
        reasons.append("claim too long")
    if not evidence_ids:
        reasons.append("cites no evidence")
    unknown_ids = [e for e in evidence_ids if e not in pack.items]
    if unknown_ids:
        reasons.append(f"cites evidence that does not exist: {', '.join(unknown_ids)}")
    unknown_keys = [k for k in fact_keys if k not in pack.facts]
    if unknown_keys:
        reasons.append(f"cites facts that do not exist: {', '.join(unknown_keys)}")
    allowed = Allowed()
    for key in fact_keys:
        if key in pack.facts:
            collect(pack.facts[key], allowed, key)
    for eid in evidence_ids:
        if (item := pack.items.get(eid)) is None:
            continue
        collect_evidence(item, allowed)
        for ns in pack.eid_signals.get(eid, []):
            collect({k: v for k, v in pack.facts.items() if k.startswith(f"{ns}.")}, allowed)
    reasons += _content_reasons(text, allowed, pack)
    return ClaimVerdict(text, evidence_ids, fact_keys, not reasons, reasons)


def check_free_text(text: str, pack: EvidencePack) -> list[str]:
    """Headline and blind-spot note: no citations, so only account-level numbers (renewal date, days
    to renewal, ARR, scores) and evidence dates are allowed; names and money are checked as usual."""
    if not text.strip():
        return []
    allowed = Allowed()
    collect({k: v for k, v in pack.facts.items() if k.startswith("account.")}, allowed)
    for item in pack.items.values():
        collect((item.date or "")[:10], allowed)
    return _content_reasons(text, allowed, pack)


@dataclass
class CheckReport:
    passed: list[ClaimVerdict]
    failed: list[ClaimVerdict]
    headline_reasons: list[str]
    note_reasons: list[str]

    @property
    def all_ok(self) -> bool:
        return bool(self.passed) and not self.failed and not self.headline_reasons and not self.note_reasons

    def summary(self) -> dict[str, Any]:
        return {
            "passed": len(self.passed),
            "failed": [{"text": v.text, "reasons": v.reasons} for v in self.failed],
            "headline_reasons": self.headline_reasons,
            "note_reasons": self.note_reasons,
        }


def check_explanation(
    headline: str, claims: list[dict[str, Any]], note: str, pack: EvidencePack
) -> CheckReport:
    verdicts = [
        check_claim(c["text"], list(c.get("evidence_ids", [])), list(c.get("fact_keys", [])), pack)
        for c in claims
    ]
    return CheckReport(
        passed=[v for v in verdicts if v.ok],
        failed=[v for v in verdicts if not v.ok],
        headline_reasons=check_free_text(headline, pack),
        note_reasons=check_free_text(note, pack),
    )
