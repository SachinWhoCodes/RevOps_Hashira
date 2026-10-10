"""The single door every AI call goes through.

In order, every call:
1. returns at once if AI is switched off (``AI_ENABLED=false``) or no key is configured;
2. masks personal identifiers in the input;
3. returns a cached, already-validated answer for an identical input (no cost);
4. refuses to run once today's spend reaches ``AI_DAILY_BUDGET_USD``;
5. asks for structured output validated against a Pydantic schema (``messages.parse``);
6. handles refusals: Opus 5.5 requests carry the server-side fallback (``fallbacks="default"``,
   beta ``server-side-fallback-2026-07-01``); Haiku 5.5 has no server-side fallback, so a refusal
   is returned as a failure and the caller degrades (e.g. leaves an email unlabelled);
7. logs model, prompt version, tokens, cost, latency and outcome to ``llm_call``.

The gateway has no tools and no write access to anything outside our database (plan rule R1).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import anthropic
import structlog
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from casefile.ai.masking import mask
from casefile.ai.pricing import cost_usd
from casefile.config import get_settings
from casefile.db.models import LlmCache, LlmCall

log = structlog.get_logger()

FALLBACK_BETA = "server-side-fallback-2026-07-01"
PROMPTS = Path(__file__).parent / "prompts"


@dataclass(frozen=True)
class RoleConfig:
    model: str
    effort: str
    max_tokens: int
    server_fallback: bool


ROLES: dict[str, RoleConfig] = {
    # Cheap, fast, high-volume: one call per customer email.
    "reader": RoleConfig("claude-haiku-5-5", "low", 4000, server_fallback=False),
    # Writing the case explanation and email drafts: quality matters, volume is low.
    "writer": RoleConfig("claude-opus-5-5", "medium", 16000, server_fallback=True),
}


def load_prompt(name: str) -> tuple[str, str]:
    """Prompts are versioned files: ``<name>.v<N>.md``. Returns (text, version) of the newest."""
    files = sorted(PROMPTS.glob(f"{name}.v*.md"), key=lambda p: int(p.stem.rsplit(".v", 1)[1]))
    if not files:
        raise FileNotFoundError(f"no prompt named {name}")
    return files[-1].read_text(), files[-1].stem


@dataclass
class AiResult[T: BaseModel]:
    output: T | None
    ok: bool
    error: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    cached: bool = False
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


def cache_key(
    prompt_version: str, model: str, effort: str, system: str, content: str, schema: type[BaseModel]
) -> str:
    material = json.dumps(
        [prompt_version, model, effort, system, content, schema.model_json_schema()], sort_keys=True
    )
    return hashlib.sha256(material.encode()).hexdigest()


class AiGateway:
    def __init__(self, session: Session, org_id: uuid.UUID, client: Any | None = None) -> None:
        self.session = session
        self.org_id = org_id
        self._client = client
        self.settings = get_settings()

    # ------------------------------------------------------------ helpers

    def client(self) -> Any:
        if self._client is None:
            key = self.settings.anthropic_api_key
            self._client = anthropic.Anthropic(
                api_key=key.get_secret_value() if key else None, max_retries=3, timeout=180.0
            )
        return self._client

    def available(self) -> tuple[bool, str | None]:
        if not self.settings.ai_enabled:
            return False, "ai_disabled"
        if self._client is None and not self.settings.anthropic_api_key:
            return False, "no_api_key"
        return True, None

    def spent_today(self) -> float:
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        total = self.session.scalar(
            select(func.coalesce(func.sum(LlmCall.cost_usd), 0)).where(
                LlmCall.org_id == self.org_id, LlmCall.created_at >= start
            )
        )
        return float(total or 0)

    def _log(self, **fields: Any) -> None:
        # Explicit timestamp: the database default is the *transaction* start, which would give every
        # call in a long run the same time and break "spent today" and per-run cost accounting.
        self.session.add(LlmCall(org_id=self.org_id, created_at=datetime.now(UTC), **fields))
        self.session.flush()

    # ------------------------------------------------------------ the call

    def structured[T: BaseModel](
        self,
        *,
        purpose: str,
        role: str,
        system: str,
        content: str,
        schema: type[T],
        prompt_version: str,
        account_id: uuid.UUID | None = None,
    ) -> AiResult[T]:
        cfg = ROLES[role]
        ok, why = self.available()
        if not ok:
            return AiResult(None, False, why, prompt_version=prompt_version)
        content = mask(content)
        key = cache_key(prompt_version, cfg.model, cfg.effort, system, content, schema)
        if (hit := self.session.get(LlmCache, key)) is not None:
            return AiResult(
                schema.model_validate(hit.output),
                True,
                model=hit.model,
                prompt_version=prompt_version,
                cached=True,
            )
        if self.spent_today() >= self.settings.ai_daily_budget_usd:
            log.warning("ai_budget_exceeded", purpose=purpose, budget=self.settings.ai_daily_budget_usd)
            return AiResult(None, False, "budget_exceeded", prompt_version=prompt_version)

        params: dict[str, Any] = {
            "model": cfg.model,
            "max_tokens": cfg.max_tokens,
            # The frozen system prompt goes first and is marked cacheable; it only caches when
            # it is longer than the model's minimum cacheable prefix.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": content}],
            "output_format": schema,
            "output_config": {"effort": cfg.effort},
        }
        started = time.perf_counter()
        try:
            if cfg.server_fallback:
                resp = self.client().beta.messages.parse(**params, betas=[FALLBACK_BETA], fallbacks="default")
            else:
                resp = self.client().messages.parse(**params)
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            failure = f"{type(exc).__name__}: {getattr(exc, 'message', str(exc))}"[:500]
            self._log(
                purpose=purpose,
                model=cfg.model,
                prompt_version=prompt_version,
                ok=False,
                error=failure,
                latency_ms=int((time.perf_counter() - started) * 1000),
                account_id=account_id,
            )
            log.error("ai_call_failed", purpose=purpose, error=failure)
            return AiResult(None, False, failure, model=cfg.model, prompt_version=prompt_version)
        latency = int((time.perf_counter() - started) * 1000)

        usage = resp.usage
        tokens = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_read_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            "cache_write_tokens": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        }
        served_by = str(getattr(resp, "model", cfg.model))
        cost = cost_usd(
            served_by,
            input_tokens=tokens["input_tokens"],
            output_tokens=tokens["output_tokens"],
            cache_read_tokens=tokens["cache_read_tokens"],
            cache_write_tokens=tokens["cache_write_tokens"],
        )
        stop = getattr(resp, "stop_reason", None)
        error: str | None = None
        output: T | None = None
        if stop == "refusal":
            details = getattr(resp, "stop_details", None)
            error = f"refusal:{getattr(details, 'category', None)}"
        elif stop == "max_tokens":
            error = "max_tokens"
        else:
            output = getattr(resp, "parsed_output", None)
            if output is None:
                error = "unparsed_output"
        self._log(
            purpose=purpose,
            model=served_by,
            prompt_version=prompt_version,
            cost_usd=Decimal(str(cost)),
            latency_ms=latency,
            stop_reason=stop,
            ok=output is not None,
            error=error,
            account_id=account_id,
            **tokens,
        )
        if output is None:
            log.warning("ai_no_output", purpose=purpose, error=error)
            return AiResult(
                None,
                False,
                error,
                model=served_by,
                prompt_version=prompt_version,
                cost_usd=cost,
                latency_ms=latency,
                input_tokens=tokens["input_tokens"],
                output_tokens=tokens["output_tokens"],
            )
        self.session.execute(
            insert(LlmCache)
            .values(
                key=key,
                org_id=self.org_id,
                purpose=purpose,
                model=served_by,
                prompt_version=prompt_version,
                output=output.model_dump(mode="json"),
            )
            .on_conflict_do_nothing(index_elements=["key"])
        )
        return AiResult(
            output,
            True,
            model=served_by,
            prompt_version=prompt_version,
            cost_usd=cost,
            latency_ms=latency,
            input_tokens=tokens["input_tokens"],
            output_tokens=tokens["output_tokens"],
        )

    # ------------------------------------------------------------ batch mode (backfills, 50% price)

    def submit_batch[T: BaseModel](
        self,
        *,
        purpose: str,
        role: str,
        system: str,
        items: list[tuple[str, str]],
        schema: type[T],
        prompt_version: str,
    ) -> tuple[str | None, dict[str, AiResult[T]], dict[str, str]]:
        """Submit (custom_id, content) items as one Message Batch. Returns the batch id (None if
        nothing needed sending), results already available from the cache, and each pending
        item's cache key. Server-side fallbacks are not available on the Batches API."""
        cfg = ROLES[role]
        ok, why = self.available()
        if not ok:
            return None, {cid: AiResult(None, False, why) for cid, _ in items}, {}
        if self.spent_today() >= self.settings.ai_daily_budget_usd:
            return None, {cid: AiResult(None, False, "budget_exceeded") for cid, _ in items}, {}
        ready: dict[str, AiResult[T]] = {}
        keys: dict[str, str] = {}
        requests = []
        output_config = {
            "effort": cfg.effort,
            "format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)},
        }
        for cid, raw in items:
            content = mask(raw)
            key = cache_key(prompt_version, cfg.model, cfg.effort, system, content, schema)
            if (hit := self.session.get(LlmCache, key)) is not None:
                ready[cid] = AiResult(
                    schema.model_validate(hit.output),
                    True,
                    model=hit.model,
                    cached=True,
                    prompt_version=prompt_version,
                )
                continue
            keys[cid] = key
            requests.append(
                {
                    "custom_id": cid,
                    "params": {
                        "model": cfg.model,
                        "max_tokens": cfg.max_tokens,
                        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                        "messages": [{"role": "user", "content": content}],
                        "output_config": output_config,
                    },
                }
            )
        if not requests:
            return None, ready, keys
        batch = self.client().messages.batches.create(requests=requests)
        log.info("ai_batch_submitted", purpose=purpose, batch=batch.id, requests=len(requests))
        return str(batch.id), ready, keys

    def collect_batch[T: BaseModel](
        self,
        batch_id: str,
        *,
        purpose: str,
        schema: type[T],
        prompt_version: str,
        keys: dict[str, str],
        poll_seconds: float = 30,
        timeout_seconds: float = 24 * 3600,
        sleep: Any = time.sleep,
    ) -> dict[str, AiResult[T]]:
        client = self.client()
        waited = 0.0
        while client.messages.batches.retrieve(batch_id).processing_status != "ended":
            if waited >= timeout_seconds:
                raise TimeoutError(f"batch {batch_id} not finished after {timeout_seconds}s")
            sleep(poll_seconds)
            waited += poll_seconds
        results: dict[str, AiResult[T]] = {}
        for entry in client.messages.batches.results(batch_id):
            cid = entry.custom_id
            if entry.result.type != "succeeded":
                results[cid] = AiResult(
                    None, False, f"batch_{entry.result.type}", prompt_version=prompt_version
                )
                continue
            msg = entry.result.message
            usage = msg.usage
            tokens = {
                "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
                "cache_read_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                "cache_write_tokens": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
            }
            cost = cost_usd(
                str(msg.model),
                batch=True,
                input_tokens=tokens["input_tokens"],
                output_tokens=tokens["output_tokens"],
                cache_read_tokens=tokens["cache_read_tokens"],
                cache_write_tokens=tokens["cache_write_tokens"],
            )
            output: T | None = None
            error: str | None = None
            if msg.stop_reason == "refusal":
                error = "refusal"
            else:
                text = next((b.text for b in msg.content if b.type == "text"), "")
                try:
                    output = schema.model_validate_json(text)
                except ValueError as exc:
                    error = f"invalid_json: {exc}"[:300]
            self._log(
                purpose=purpose,
                model=str(msg.model),
                prompt_version=prompt_version,
                cost_usd=Decimal(str(cost)),
                stop_reason=msg.stop_reason,
                ok=output is not None,
                error=error,
                **tokens,
            )
            if output is not None and cid in keys:
                self.session.execute(
                    insert(LlmCache)
                    .values(
                        key=keys[cid],
                        org_id=self.org_id,
                        purpose=purpose,
                        model=str(msg.model),
                        prompt_version=prompt_version,
                        output=output.model_dump(mode="json"),
                    )
                    .on_conflict_do_nothing(index_elements=["key"])
                )
            results[cid] = AiResult(
                output,
                output is not None,
                error,
                model=str(msg.model),
                prompt_version=prompt_version,
                cost_usd=cost,
                input_tokens=tokens["input_tokens"],
                output_tokens=tokens["output_tokens"],
            )
        return results
