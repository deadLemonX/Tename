"""Unit tests for pricing lookup and cost computation."""

from __future__ import annotations

from tename.router.pricing import (
    compute_cost_usd,
    load_default_pricing_table,
    lookup_pricing,
)
from tename.router.types import Pricing, Usage


def test_default_table_includes_opus_4_6() -> None:
    table = load_default_pricing_table()
    assert "anthropic" in table
    pricing = table["anthropic"]["claude-opus-4-6"]
    assert pricing.input_per_million == 15.00
    assert pricing.output_per_million == 75.00
    assert pricing.cached_input_per_million == 1.50


def test_lookup_pricing_falls_back_to_default_table() -> None:
    p = lookup_pricing(provider="anthropic", model_id="claude-opus-4-6")
    assert p is not None
    assert p.input_per_million == 15.00


def test_lookup_pricing_unknown_returns_none() -> None:
    assert lookup_pricing(provider="anthropic", model_id="ghost") is None
    assert lookup_pricing(provider="mystery", model_id="x") is None


def test_lookup_pricing_override_wins() -> None:
    override = Pricing(input_per_million=1.0, output_per_million=2.0)
    p = lookup_pricing(
        provider="anthropic", model_id="claude-opus-4-6", override=override
    )
    assert p is override


def test_compute_cost_basic() -> None:
    pricing = Pricing(input_per_million=10.0, output_per_million=20.0)
    usage = Usage(input_tokens=1_000_000, output_tokens=500_000)
    # 1M * $10/1M + 500k * $20/1M = 10 + 10 = 20
    assert compute_cost_usd(usage, pricing) == 20.0


def test_compute_cost_splits_cached_input() -> None:
    pricing = Pricing(
        input_per_million=10.0,
        output_per_million=20.0,
        cached_input_per_million=1.0,
    )
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=0,
        cached_input_tokens=500_000,
    )
    # 500k uncached * $10/1M = $5 + 500k cached * $1/1M = $0.50 → $5.50
    assert compute_cost_usd(usage, pricing) == 5.50


def test_compute_cost_reasoning_tokens_bill_as_output() -> None:
    pricing = Pricing(input_per_million=10.0, output_per_million=20.0)
    usage = Usage(input_tokens=0, output_tokens=500_000, reasoning_tokens=500_000)
    # 1M * $20/1M = $20
    assert compute_cost_usd(usage, pricing) == 20.0


def test_compute_cost_no_pricing_returns_none() -> None:
    usage = Usage(input_tokens=1000, output_tokens=500)
    assert compute_cost_usd(usage, None) is None


def test_compute_cost_cached_fallback_to_input_rate() -> None:
    # No cached_input_per_million → cached tokens billed at input rate.
    pricing = Pricing(input_per_million=10.0, output_per_million=20.0)
    usage = Usage(input_tokens=1_000_000, cached_input_tokens=500_000)
    # Uncached 500k * $10/1M = $5; cached 500k * $10/1M = $5 → $10
    assert compute_cost_usd(usage, pricing) == 10.0
