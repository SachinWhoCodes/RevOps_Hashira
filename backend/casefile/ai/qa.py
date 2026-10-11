"""Ask about an account (Phase 6.4): Claude answers questions using read-only tools over our DB.

Claude Opus 5.5 runs through the SDK tool runner with five read-only tools — ``get_signals``,
``get_timeline``, ``search_activities``, ``get_usage_series``, ``get_contacts`` — all scoped to one
account as of one moment (the same snapshot the score uses, rule R3). Nothing here can write: the
tools only read the snapshot, and the AI zone holds no write credentials (rule R1).

Every record a tool returns is registered in the account's evidence pack under an evidence ID, and
every number a tool computes becomes a fact key. The answer comes back as structured sentences that
cite those IDs and keys, and each sentence goes through the same claim checker as case explanations
(rule R2): a sentence with a number, date or name the cited records don't support is removed before
anyone sees it. Customer text reaches Claude as marked, masked data; emails flagged as injection
attempts are withheld (rule R6).

Cost control: daily budget check before starting, at most ``MAX_ITERATIONS`` model turns, every turn
logged to ``llm_call``, and identical questions about an unchanged account are answered from cache.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any

import anthropic
import structlog
from anthropic import beta_tool
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from casefile.ai.checker import check_claim, check_free_text
from casefile.ai.evidence import SNIPPET_CHARS, EvidencePack, PackItem, build_pack, render_for_writer
from casefile.ai.gateway import FALLBACK_BETA, AiGateway, load_prompt
from casefile.ai.masking import mask
from casefile.ai.pricing import cost_usd
from casefile.cases.relationships import relationship_map
from casefile.cases.service import Assessed
from casefile.db.models import Account, AppUser, LlmCache
from casefile.timeline.snapshot import ActivityView, Snapshot

log = structlog.get_logger()

QA_MODEL = "claude-opus-5-5"
QA_EFFORT = "medium"
MAX_TOKENS = 8000
MAX_ITERATIONS = 8
TIMELINE_LIMIT = 40
# Part of the answer cache key: bump when the tools or what they return change, so answers built on
# the old tool output are not served from cache.
TOOLS_VERSION = "qa-tools-2"


class QaSentence(BaseModel):
    text: str = Field(description="One short sentence, one idea.")
    evidence_ids: list[str] = Field(description="Evidence IDs (E1, E2, ...) this sentence rests on.")
    fact_keys: list[str] = Field(default_factory=list, description="Fact keys of any number used.")


class QaAnswer(BaseModel):
    sentences: list[QaSentence]
    gaps: str = Field(default="", description="What the data does not show, in words. No numbers.")


@dataclass
class QaResult:
    question: str
    ok: bool
    sentences: list[dict[str, Any]] = field(default_factory=list)  # verified, with evidence refs
    removed: list[dict[str, Any]] = field(default_factory=list)  # failed the checker, with reasons
    gaps: str = ""
    error: str | None = None
    tools_used: list[str] = field(default_factory=list)
    iterations: int = 0
    cost_usd: float = 0.0
    cached: bool = False
    model: str | None = None
    prompt_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "person"


# ---------------------------------------------------------------- the read-only tools


class AccountTools:
    """Read-only views of one account as of one moment. Everything returned is registered in the
    evidence pack, so the checker can verify any sentence that cites it."""

    def __init__(self, snap: Snapshot, pack: EvidencePack, rendered_signals: str) -> None:
        self.snap = snap
        self.pack = pack
        self.rendered_signals = rendered_signals
        self.used: list[str] = []

    # registration ------------------------------------------------------------

    def _add_corpus(self, *texts: str) -> None:
        self.pack.corpus += "\n" + "\n".join(t for t in texts if t)

    def _register(self, kind: str, ref: str, label: str, day: str | None, text: str) -> str:
        if ref in self.pack.by_ref:
            return self.pack.by_ref[ref]
        eid = f"E{len(self.pack.items) + 1}"
        self.pack.items[eid] = PackItem(eid, kind, ref, label, day, text)
        self.pack.by_ref[ref] = eid
        self._add_corpus(label, text)
        return eid

    def _fact(self, key: str, value: Any) -> str:
        self.pack.facts[key] = value
        if isinstance(value, str):
            self._add_corpus(value)
        return key

    def _activity(self, a: ActivityView) -> str:
        snap = self.snap
        day = snap.local_date(a.occurred_at).isoformat()
        when = "scheduled" if a.occurred_at > snap.as_of else a.direction
        if a.injection_flag:
            reason = a.injection_reason or "flagged"
            text = f"[content withheld: flagged as a possible injection attempt ({reason})]"
        else:
            body = " ".join((a.snippet or "").split())
            text = mask(f"{a.type} ({when}) {day}: {a.subject or ''} — {body}")[:SNIPPET_CHARS]
        label = f"{a.type.capitalize()} {day}: {a.subject or ''}"[:120]
        eid = self._register("activity", str(a.id), label, a.occurred_at.isoformat(), text)
        who = a.from_email or ", ".join(a.participant_emails[:3])
        flags = [
            f
            for f, on in (
                ("auto-reply", a.extra.get("auto_reply")),
                ("bounce", a.extra.get("bounce")),
                ("cancelled", a.cancelled),
            )
            if on
        ]
        return (
            f'<evidence id="{eid}" kind="{a.type}" date="{day}" direction="{when}" from="{mask(who)}"'
            f"{' flags=' + chr(34) + ','.join(flags) + chr(34) if flags else ''}>{text}</evidence>"
        )

    # tools ---------------------------------------------------------------------

    def tools(self) -> list[Any]:
        snap = self.snap

        @beta_tool
        def get_signals() -> str:
            """The account's header (owner, ARR, renewal date, risk and opportunity scores), the
            signals the rules detected with their facts and evidence, contacts and blind spots."""
            self.used.append("get_signals")
            return self.rendered_signals

        @beta_tool
        def get_timeline(kind: str = "all", days: int = 90, limit: int = 25) -> str:
            """Recent emails, meetings, notes and tasks, newest first, plus meetings already booked.

            Args:
                kind: "all", "email", "meeting", "note", "task" or "call".
                days: How many days back to look (1 to 400).
                limit: Maximum records to return (1 to 40).
            """
            self.used.append("get_timeline")
            days, limit = max(1, min(int(days), 400)), max(1, min(int(limit), TIMELINE_LIMIT))
            since = snap.as_of - timedelta(days=days)
            match = [
                a
                for a in snap.activities
                if (kind == "all" or a.type == kind) and since <= a.occurred_at <= snap.as_of
            ]
            upcoming = [
                a
                for a in snap.activities
                if a.type == "meeting" and a.occurred_at > snap.as_of and not a.cancelled
            ]
            key = self._fact(f"timeline.{_slug(kind)}.last_{days}_days.count", len(match))
            window = self._fact(f"timeline.window_{days}.days", days)
            lines = [f"{len(match)} records in the last {days} days (facts {key}, {window}); newest first:"]
            lines += [
                self._activity(a) for a in sorted(match, key=lambda a: a.occurred_at, reverse=True)[:limit]
            ]
            if upcoming:
                ukey = self._fact("timeline.meetings_booked.count", len(upcoming))
                lines.append(f"Meetings already booked (fact {ukey}):")
                lines += [self._activity(a) for a in sorted(upcoming, key=lambda a: a.occurred_at)[:5]]
            return "\n".join(lines)

        @beta_tool
        def search_activities(query: str, limit: int = 15) -> str:
            """Find emails, meetings and notes whose subject, text or sender contains the words.

            Args:
                query: Words to look for (case does not matter), e.g. "seats" or "Datawise".
                limit: Maximum records to return (1 to 40).
            """
            self.used.append("search_activities")
            limit = max(1, min(int(limit), TIMELINE_LIMIT))
            words = [w for w in query.lower().split() if w]
            found = []
            for a in snap.activities:
                if a.occurred_at > snap.as_of and a.type != "meeting":
                    continue
                hay = f"{a.subject or ''} {a.from_email or ''}" + (
                    "" if a.injection_flag else f" {a.snippet or ''}"
                )
                if words and all(w in hay.lower() for w in words):
                    found.append(a)
            key = self._fact(f"search.{_slug(query)}.count", len(found))
            lines = [f"{len(found)} records match {query!r} (fact {key}); newest first:"]
            lines += [
                self._activity(a) for a in sorted(found, key=lambda a: a.occurred_at, reverse=True)[:limit]
            ]
            return "\n".join(lines)

        @beta_tool
        def get_usage_series(weeks: int = 12) -> str:
            """Weekly product usage: average daily active users, key actions a day and seats in use.

            Args:
                weeks: How many recent weeks (1 to 52).
            """
            self.used.append("get_usage_series")
            weeks = max(1, min(int(weeks), 52))
            lo = snap.today - timedelta(weeks=weeks)
            rows = [u for u in snap.usage if lo <= u.date < snap.today]
            if not rows:
                return "No product usage data for this account in that period."
            buckets: dict[str, list[Any]] = defaultdict(list)
            for u in rows:
                buckets[(u.date - timedelta(days=u.date.weekday())).isoformat()].append(u)
            start, end = rows[0].date.isoformat(), rows[-1].date.isoformat()
            eid = self._register(
                "usage",
                f"usage:{start}:{end}",
                f"Daily usage {start} to {end}",
                end,
                f"Daily product usage from {start} to {end}",
            )
            lines = [
                f'<evidence id="{eid}" kind="usage">Weekly averages (seats purchased: '
                f"{snap.account.seats_purchased}, fact account.seats_purchased)</evidence>"
            ]
            for week, us in sorted(buckets.items()):
                users = round(sum(u.active_users for u in us) / len(us), 1)
                actions = round(sum(u.key_actions for u in us) / len(us), 1)
                seats = [u.seats_used for u in us if u.seats_used is not None]
                k = f"usage.week_of_{week}"
                self._fact(f"{k}.active_users_avg", users)
                self._fact(f"{k}.key_actions_avg", actions)
                line = (
                    f"week of {week}: active users {users} ({k}.active_users_avg), "
                    f"key actions a day {actions} ({k}.key_actions_avg)"
                )
                if seats:
                    self._fact(f"{k}.seats_used_max", max(seats))
                    line += f", seats used up to {max(seats)} ({k}.seats_used_max)"
                lines.append(line)
            return "\n".join(lines)

        @beta_tool
        def get_contacts() -> str:
            """Everyone we know at the customer: role, title, status, and how often they wrote to us,
            we wrote to them and met us in the last 90 days, and when we last heard from them."""
            self.used.append("get_contacts")
            m = relationship_map(snap)
            self._fact("people.window_days", m["window_days"])
            self._fact("people.active_window_days", m["active_days"])
            self._fact("people.known_count", m["known_people"])
            lines = [
                f"Counts cover the last {m['window_days']} days (fact people.window_days); "
                f"active means wrote to us or met us in the last {m['active_days']} days "
                "(fact people.active_window_days).",
                f"{m['active_people']} of {m['known_people']} people wrote to us or met us in the last "
                f"{m['active_days']} days (facts {self._fact('people.active_count', m['active_people'])}, "
                f"people.known_count, people.active_window_days).",
            ]
            for p in m["people"]:
                name = p["name"] or p["email"]
                k = f"people.{_slug(name)}"
                self._fact(f"{k}.emails_from_90d", p["emails_from"])
                self._fact(f"{k}.emails_to_90d", p["emails_to"])
                self._fact(f"{k}.meetings_90d", p["meetings"])
                if p["days_since_heard"] is not None:
                    self._fact(f"{k}.days_since_heard", p["days_since_heard"])
                    self._fact(f"{k}.last_heard_from", p["last_heard_from"])
                text = (
                    f"{name}, {p['title'] or 'no title'}, role {p['role']}, status {p['status']}, "
                    f"{'in the CRM' if p['origin'] == 'crm' else 'seen in email/calendar only'}"
                )
                eid = self._register("contact", f"{p['id']}:contact", name, p["last_heard_from"], text)
                heard = (
                    f"last heard from {p['last_heard_from']}" if p["last_heard_from"] else "never heard from"
                )
                lines.append(
                    f'<evidence id="{eid}" kind="contact">{text}; {heard}; last 90 days: {p["emails_from"]} '
                    f"emails from them, {p['emails_to']} to them, {p['meetings']} meetings "
                    f"(facts {k}.*)</evidence>"
                )
            return "\n".join(lines)

        return [get_signals, get_timeline, search_activities, get_usage_series, get_contacts]


# ---------------------------------------------------------------- running a question


def _fingerprint(snap: Snapshot, assessed: Assessed) -> str:
    latest = max((a.known_at for a in snap.activities), default=None)
    material = [
        str(snap.account.id),
        snap.today.isoformat(),
        assessed.score.signature,
        latest.isoformat() if latest else "",
        len(snap.activities),
        len(snap.usage),
    ]
    return hashlib.sha256(json.dumps(material).encode()).hexdigest()


def _run(client: Any, params: dict[str, Any]) -> list[Any]:
    """Drive the SDK tool runner; returns every model turn (each is one API call)."""
    runner = client.beta.messages.tool_runner(**params)
    return list(runner)


def ask(
    session: Session,
    gateway: AiGateway,
    snap: Snapshot,
    assessed: Assessed,
    question: str,
    *,
    use_cache: bool = True,
) -> QaResult:
    system, version = load_prompt("account_qa")
    result = QaResult(question=question, ok=False, model=QA_MODEL, prompt_version=version)
    question = question.strip()[:500]
    if not question:
        result.error = "empty question"
        return result
    ok, why = gateway.available()
    if not ok:
        result.error = why
        return result
    key = hashlib.sha256(
        json.dumps(
            [
                version,
                TOOLS_VERSION,
                QA_MODEL,
                QA_EFFORT,
                " ".join(question.lower().split()),
                _fingerprint(snap, assessed),
            ]
        ).encode()
    ).hexdigest()
    if use_cache and (hit := session.get(LlmCache, key)) is not None:
        cached = QaResult(**hit.output)
        cached.cached, cached.cost_usd = True, 0.0
        return cached
    if gateway.spent_today() >= gateway.settings.ai_daily_budget_usd:
        result.error = "budget_exceeded"
        return result

    account = session.get(Account, snap.account.id)
    owner = session.get(AppUser, account.owner_user_id) if account and account.owner_user_id else None
    b = assessed.score.breakdown
    pack = build_pack(
        snap,
        assessed.hits,
        owner_name=owner.name if owner else None,
        risk=assessed.score.risk,
        opportunity=assessed.score.opportunity,
    )
    points = {line["signal"]: line["points"] for line in b["risk"]["lines"] + b["opportunity"]["lines"]}
    rendered = render_for_writer(
        pack,
        snap,
        assessed.hits,
        owner_name=owner.name if owner else None,
        confidence=assessed.score.confidence,
        blind_spots=assessed.blind_spots,
        points=points,
    )
    tools = AccountTools(snap, pack, rendered)
    params: dict[str, Any] = {
        "model": QA_MODEL,
        "max_tokens": MAX_TOKENS,
        "max_iterations": MAX_ITERATIONS,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "tools": tools.tools(),
        "messages": [
            {"role": "user", "content": f"Account: {snap.account.name}\nQuestion: {mask(question)}"}
        ],
        "output_format": QaAnswer,
        "output_config": {"effort": QA_EFFORT},
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
    }
    started = time.perf_counter()
    try:
        turns = _run(gateway.client(), params)
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
        result.error = f"{type(exc).__name__}: {getattr(exc, 'message', str(exc))}"[:500]
        gateway._log(
            purpose="account_qa",
            model=QA_MODEL,
            prompt_version=version,
            ok=False,
            error=result.error,
            latency_ms=int((time.perf_counter() - started) * 1000),
            account_id=snap.account.id,
        )
        return result
    latency = int((time.perf_counter() - started) * 1000)
    result.iterations, result.tools_used = len(turns), list(dict.fromkeys(tools.used))
    final = turns[-1] if turns else None
    for i, turn in enumerate(turns):
        u = turn.usage
        tokens = {
            "input_tokens": int(getattr(u, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
            "cache_read_tokens": int(getattr(u, "cache_read_input_tokens", 0) or 0),
            "cache_write_tokens": int(getattr(u, "cache_creation_input_tokens", 0) or 0),
        }
        served = str(getattr(turn, "model", QA_MODEL))
        cost = cost_usd(
            served,
            input_tokens=tokens["input_tokens"],
            output_tokens=tokens["output_tokens"],
            cache_read_tokens=tokens["cache_read_tokens"],
            cache_write_tokens=tokens["cache_write_tokens"],
        )
        result.cost_usd += cost
        result.model = served
        gateway._log(
            purpose="account_qa",
            model=served,
            prompt_version=version,
            cost_usd=Decimal(str(cost)),
            latency_ms=latency if i == len(turns) - 1 else 0,
            stop_reason=turn.stop_reason,
            ok=turn.stop_reason not in ("refusal", "max_tokens"),
            account_id=snap.account.id,
            **tokens,
        )
    if final is None:
        result.error = "no answer"
        return result
    if final.stop_reason == "refusal":
        result.error = f"refusal:{getattr(getattr(final, 'stop_details', None), 'category', None)}"
        return result
    answer = getattr(final, "parsed_output", None)
    if not isinstance(answer, QaAnswer):
        result.error = "no structured answer" if final.stop_reason != "max_tokens" else "max_tokens"
        return result

    for s in answer.sentences:
        verdict = check_claim(s.text, s.evidence_ids, s.fact_keys, pack)
        row = {"text": s.text, "evidence": pack.item_refs(s.evidence_ids), "fact_keys": s.fact_keys}
        if verdict.ok:
            result.sentences.append(row)
        else:
            result.removed.append({**row, "reasons": verdict.reasons})
    result.gaps = answer.gaps if not check_free_text(answer.gaps, pack) else ""
    result.ok = bool(result.sentences) or bool(result.gaps)
    if result.ok:
        session.execute(
            insert(LlmCache)
            .values(
                key=key,
                org_id=gateway.org_id,
                purpose="account_qa",
                model=result.model or QA_MODEL,
                prompt_version=version,
                output={**result.to_dict(), "cached": False},
            )
            .on_conflict_do_nothing(index_elements=["key"])
        )
    return result
