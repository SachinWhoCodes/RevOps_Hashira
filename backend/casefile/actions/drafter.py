"""Gmail draft text: the only part of a change set worded by AI (Claude Opus 5.5), and checked in code.

- The writer sees the play's purpose, the recipient, and the case's *verified* claims as internal
  context. Emails flagged as injection attempts never reach it (they are withheld from claims).
- Money filter: any mention of price, discount, refund, credit, contract, invoice, "% off", free
  months... blocks the line ("money talk stays human"). Blocked lines can never be executed.
- Other checks (names, numbers, dates, internal jargon) get one retry with the reasons, then a
  plain template is used instead, and the line says so.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from casefile.ai.checker import COMMON
from casefile.ai.gateway import AiGateway, load_prompt

MAX_ATTEMPTS = 2

MONEY_TALK = re.compile(
    r"\b(discount\w*|refund\w*|credits?|price[ds]?|pricing|costs?|fees?|invoices?|invoic\w+|billing|"
    r"payments?|contracts?|rebates?|coupons?|promo\w*|waive\w*|free (?:months?|seats?|upgrades?|trial)|"
    r"complimentary)\b|\d+\s?%\s?off|percent off",
    re.I,
)
INTERNAL = re.compile(
    r"\b(risk|churn\w*|scores?|signals?|casefile|health ?score|our (?:data|records|system) show\w*|"
    r"we (?:noticed|saw|see) (?:that )?(?:your )?(?:usage|activity|logins?))\b",
    re.I,
)
PROMISE = re.compile(r"\b(guarantee\w*|we promise|promised|commit to|will definitely)\b", re.I)
NUMBER = re.compile(r"\d+")
MINUTES = re.compile(r"\b30[- ]?(?:minutes?|mins?)\b", re.I)
WORD = re.compile(r"[A-Za-z][A-Za-z'\u2019-]*")

# Ordinary words that may start a sentence or appear capitalised in an email.
EMAIL_WORDS = set(
    """
hi hello dear hope thanks thank you i i'm i'd i'll i've it it's we we're we'd we'll our your you're
would could can let let's looking best regards kind warm cheers many happy glad please just when what
how quick wanted want feel free are is do does if as as we're with given since after before ahead
also and but so that this there here would like love keen good great sounds ok okay a an the to in
on at for of from my me any all next week monday tuesday wednesday thursday friday morning afternoon
call chat time team teams renewal year coming weeks month months over whether many keen curious
recently lately either perhaps maybe once ideally really meanwhile otherwise together finally first
then because while even both each every some most nothing anything something yes sure certainly
are were is was have has had will shall should might may did done going there's that's what's
how's who where which who's thought thinking checking supporting following hoping touching reaching
planning catching getting wondering writing helping quick short brief update catch check follow touch
base plans support rollout setup help more other new
""".split()
)
# Business words that often appear capitalised in subjects and titles ("Account owner introduction").
BUSINESS_WORDS = set(
    """
account accounts owner manager director head lead introduction introductions intro partnership
renewal renewals success plan plans planning review reviews update updates roadmap goals priorities
transition handover contact contacts relationship onboarding training workshop session sessions call
meeting meetings chat sync agenda feedback question questions next step steps catch-up follow-up
hello welcome note thoughts team teams time working day weekly monthly quarterly
""".split()
)
# At the start of a sentence, a capitalised word that looks like ordinary English is fine.
ENGLISH_SHAPE = re.compile(
    r"(ing|ed|ly|tion|sion|ment|ness|ance|ence|ity|ive|ful|less|able|ible|ise|ize|ship)$"
)
SENTENCE = re.compile(r"[^.!?\n]+")


class DraftOut(BaseModel):
    subject: str
    body: str


@dataclass
class DraftContext:
    sender_name: str
    recipient_name: str
    recipient_email: str
    recipient_role: str
    company: str
    purpose: str
    renewal_date: str | None  # ISO date
    claims: list[str] = field(default_factory=list)
    # Other people we know (contacts at the account, our own team): a draft must not name them.
    other_names: list[str] = field(default_factory=list)


@dataclass
class Drafted:
    subject: str
    body: str
    source: str  # ai | template
    blocked_reason: str | None = None
    reasons: list[str] = field(default_factory=list)
    model: str | None = None
    prompt_version: str | None = None
    cost_usd: float = 0.0


def _first(name: str) -> str:
    return (name or "").split()[0] if name and name.split() else "there"


def _renewal_forms(iso: str | None) -> set[str]:
    if not iso:
        return set()
    y, m, d = iso.split("-")
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    long = "January February March April May June July August September October November December".split()
    day = str(int(d))
    mi = int(m) - 1
    return {
        iso,
        f"{day} {months[mi]} {y}",
        f"{day} {long[mi]} {y}",
        f"{months[mi]} {day}, {y}",
        f"{long[mi]} {day}, {y}",
        f"{day} {months[mi]}",
        f"{day} {long[mi]}",
        f"{months[mi]} {day}",
        f"{long[mi]} {day}",
    }


def _human_date(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{int(d)} {'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()[int(m) - 1]} {y}"


def money_talk(text: str) -> str | None:
    m = MONEY_TALK.search(text)
    return m.group(0) if m else None


def check_draft(subject: str, body: str, ctx: DraftContext) -> list[str]:
    """Reasons the draft may not be shown (empty list = passes). Money talk is checked separately."""
    reasons: list[str] = []
    text = f"{subject}\n{body}"
    common = COMMON | EMAIL_WORDS
    if m := INTERNAL.search(text):
        reasons.append(f"mentions internal term '{m.group(0)}'")
    if m := PROMISE.search(text):
        reasons.append(f"makes a promise ('{m.group(0)}')")
    rest = MINUTES.sub(" ", text)
    for form in sorted(_renewal_forms(ctx.renewal_date), key=len, reverse=True):
        rest = rest.replace(form, " ")
    if NUMBER.search(rest):
        reasons.append("contains a number or date other than the renewal date")
    allowed_names = {
        w.lower() for w in WORD.findall(" ".join([ctx.sender_name, ctx.recipient_name, ctx.company]))
    }
    others = {w.lower() for name in ctx.other_names for w in WORD.findall(name)} - allowed_names
    plain = common | BUSINESS_WORDS
    subject_rest, _, body_rest = rest.partition("\n")
    flagged: set[str] = set()

    def judge(word: str, *, starts_sentence: bool) -> None:
        if not word[0].isupper():
            return  # only capitalised words can be names
        low = word.lower()
        low = low[:-2] if low.endswith(("'s", "\u2019s")) else low
        if low in allowed_names or word in flagged:
            return
        if low in others:
            flagged.add(word)
            reasons.append(f"names '{word}', who is neither the sender nor the recipient")
            return
        if low in plain or (word.isupper() and len(word) <= 4):
            return
        if starts_sentence and len(low) > 4 and ENGLISH_SHAPE.search(low):
            return
        # Mid-sentence, an unknown capitalised word is most likely an invented person or company.
        flagged.add(word)
        reasons.append(f"uses '{word}', which is not the sender, recipient, company or a plain word")

    for word in WORD.findall(subject_rest):  # subjects are often in title case
        judge(word, starts_sentence=True)
    for sentence in SENTENCE.findall(body_rest):
        for i, word in enumerate(WORD.findall(sentence)):
            judge(word, starts_sentence=i == 0)
    if not body.strip().lower().startswith(("hi", "hello", "dear")):
        reasons.append("does not greet the recipient")
    words = len(body.split())
    if words > 200:
        reasons.append(f"too long ({words} words)")
    return reasons


def template_draft(ctx: DraftContext, *, expansion: bool) -> tuple[str, str]:
    first, me = _first(ctx.recipient_name), _first(ctx.sender_name)
    if expansion:
        subject = f"Supporting more teams at {ctx.company}"
        middle = (
            "I'd like to hear how the rollout is going and whether other teams could use some help "
            "getting set up."
        )
    else:
        subject = f"Checking in on {ctx.company}"
        middle = (
            "I'd like to hear how things are going for the team and whether there is anything we "
            "should change."
        )
        if ctx.renewal_date:
            middle += f" It would be good to talk well before the renewal on {_human_date(ctx.renewal_date)}."
    body = f"Hi {first},\n\n{middle} Would you have 30 minutes next week for a short call?\n\nBest,\n{me}"
    return subject, body


def render_context(ctx: DraftContext) -> str:
    lines = [
        f"SENDER (account owner): {ctx.sender_name}",
        f"RECIPIENT: {ctx.recipient_name} ({ctx.recipient_role.replace('_', ' ')}) at {ctx.company}",
        f"PURPOSE: {ctx.purpose}",
        f"RENEWAL DATE: {_human_date(ctx.renewal_date) if ctx.renewal_date else 'none'}",
        "",
        "INTERNAL CONTEXT (verified; for understanding only, never quote it):",
        *(f"- {c}" for c in ctx.claims),
    ]
    if not ctx.claims:
        lines.append("- none")
    return "\n".join(lines)


def draft_email(
    gateway: AiGateway | None, ctx: DraftContext, *, expansion: bool, account_id: uuid.UUID | None = None
) -> Drafted:
    if gateway is None or not gateway.available()[0]:
        subject, body = template_draft(ctx, expansion=expansion)
        return Drafted(subject, body, "template", reasons=["ai_not_used"])
    system, version = load_prompt("email_drafter")
    content = render_context(ctx)
    feedback = ""
    tried: list[dict[str, Any]] = []
    model: str | None = None
    cost = 0.0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        result = gateway.structured(
            purpose="email_drafter",
            role="writer",
            system=system,
            content=content + feedback,
            schema=DraftOut,
            prompt_version=version,
            account_id=account_id,
        )
        model = result.model or model
        cost += result.cost_usd or 0.0
        if not result.ok or result.output is None:
            tried.append({"attempt": attempt, "error": result.error})
            break
        out = result.output
        if hit := money_talk(f"{out.subject}\n{out.body}"):
            # Not retried: a draft that reaches for money is a signal for a person, not a rewrite.
            return Drafted(
                out.subject,
                out.body,
                "ai",
                blocked_reason=f"money talk stays human (mentions '{hit}')",
                model=model,
                prompt_version=version,
                cost_usd=cost,
            )
        reasons = check_draft(out.subject, out.body, ctx)
        tried.append({"attempt": attempt, "reasons": reasons})
        if not reasons:
            return Drafted(out.subject, out.body, "ai", model=model, prompt_version=version, cost_usd=cost)
        feedback = (
            "\n\nCHECKER FEEDBACK ON YOUR PREVIOUS DRAFT "
            "(it was discarded; write a new one within the rules):\n" + "\n".join(f"- {r}" for r in reasons)
        )
    subject, body = template_draft(ctx, expansion=expansion)
    flat = [r for t in tried for r in t.get("reasons", [])] or [str(t.get("error")) for t in tried]
    return Drafted(
        subject, body, "template", reasons=flat, model=model, prompt_version=version, cost_usd=cost
    )
