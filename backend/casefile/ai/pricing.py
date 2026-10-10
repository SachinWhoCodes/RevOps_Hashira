"""USD per million tokens, for logging what each AI call cost.

Source: Anthropic price list as cached in the Claude API reference on 2026-10-06. Check
https://www.anthropic.com/pricing before quoting these to a customer. Cache writes are billed at
1.25x input (5-minute cache); cache reads as listed. Batch API calls cost 50%.
Fallback targets are included because a refused Opus 5.5 request may be served by them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float

    @property
    def cache_write(self) -> float:
        return self.input * 1.25


PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(4.00, 20.00, 0.20),
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01),
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.20),
    "claude-opus-5": Price(5.00, 25.00, 0.50),
    "claude-opus-4-8": Price(5.00, 25.00, 0.50),
}


def cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    batch: bool = False,
) -> float:
    price = PRICES.get(model) or PRICES["claude-opus-5-5"]  # unknown model: price conservatively
    total = (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_read_tokens * price.cache_read
        + cache_write_tokens * price.cache_write
    ) / 1_000_000
    return round(total * (0.5 if batch else 1.0), 6)
