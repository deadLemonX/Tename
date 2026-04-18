"""Pricing table and cost calculation for the Model Router.

Pricing lives in a YAML file that ships with the wheel. Callers can also
pass a `Pricing` object on the profile to override the table per request.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from tename.router.types import Pricing, Usage

DEFAULT_PRICING_RESOURCE = "pricing.yaml"


@lru_cache(maxsize=1)
def load_default_pricing_table() -> dict[str, dict[str, Pricing]]:
    """Load the bundled pricing.yaml via importlib.resources.

    Returns a nested dict: {provider: {model_id: Pricing}}.
    Missing or malformed entries raise — this is a programming error, not
    a runtime condition.
    """
    text = resources.files("tename.router").joinpath(DEFAULT_PRICING_RESOURCE).read_text()
    raw: dict[str, Any] = yaml.safe_load(text) or {}
    table: dict[str, dict[str, Pricing]] = {}
    for provider, models in raw.items():
        table[provider] = {}
        for model_id, entry in (models or {}).items():
            table[provider][model_id] = Pricing.model_validate(entry)
    return table


def lookup_pricing(
    *, provider: str, model_id: str, override: Pricing | None = None
) -> Pricing | None:
    """Resolve pricing for a (provider, model_id) pair.

    Order: explicit override (from the profile) wins; otherwise fall back to
    the bundled default table. Returns None if nothing matches so the caller
    can report a null `cost_usd` rather than crash.
    """
    if override is not None:
        return override
    table = load_default_pricing_table()
    return table.get(provider, {}).get(model_id)


def compute_cost_usd(usage: Usage, pricing: Pricing | None) -> float | None:
    """Compute cost in USD from token counts and pricing.

    Cached input tokens are billed at `cached_input_per_million` when set,
    otherwise at the regular input rate. Non-cached input = input_tokens -
    cached_input_tokens. Reasoning tokens are billed as output tokens (they
    count against the output budget on every provider we care about in v0.1).
    """
    if pricing is None:
        return None
    uncached_input = max(usage.input_tokens - usage.cached_input_tokens, 0)
    cached_rate = (
        pricing.cached_input_per_million
        if pricing.cached_input_per_million is not None
        else pricing.input_per_million
    )
    output_equivalent = usage.output_tokens + usage.reasoning_tokens
    return round(
        (uncached_input * pricing.input_per_million) / 1_000_000
        + (usage.cached_input_tokens * cached_rate) / 1_000_000
        + (output_equivalent * pricing.output_per_million) / 1_000_000,
        8,
    )


__all__ = [
    "compute_cost_usd",
    "load_default_pricing_table",
    "lookup_pricing",
]
