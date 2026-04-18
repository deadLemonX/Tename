"""Unit tests for router types: chunk factories, Usage, profile models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tename.router.types import (
    CachingBreakpoint,
    CachingConfig,
    ErrorHandling,
    ModelChunk,
    ModelConfig,
    Pricing,
    RouterProfile,
    Sampling,
    Usage,
    done_chunk,
    error_chunk,
    text_delta,
    tool_call_delta,
    tool_call_end,
    tool_call_start,
    usage_chunk,
)


def test_text_delta_factory() -> None:
    chunk = text_delta("hello")
    assert chunk.type == "text_delta"
    assert chunk.content == {"text": "hello"}


def test_tool_call_factories_encode_schema() -> None:
    start = tool_call_start(tool_id="t1", tool_name="bash", index=0)
    delta = tool_call_delta(tool_id="t1", partial_json='{"cmd":"ls"', index=0)
    end = tool_call_end(
        tool_id="t1", tool_name="bash", tool_input={"cmd": "ls"}, index=0
    )

    assert start.content == {"tool_id": "t1", "tool_name": "bash", "index": 0}
    assert delta.content == {
        "tool_id": "t1",
        "partial_json": '{"cmd":"ls"',
        "index": 0,
    }
    assert end.content == {
        "tool_id": "t1",
        "tool_name": "bash",
        "input": {"cmd": "ls"},
        "index": 0,
    }


def test_usage_chunk_and_done_chunk() -> None:
    u = Usage(input_tokens=100, output_tokens=50)
    uc = usage_chunk(u)
    assert uc.type == "usage"
    assert uc.content["input_tokens"] == 100
    assert uc.content["output_tokens"] == 50
    assert uc.content["cost_usd"] is None

    dc = done_chunk()
    assert dc.type == "done"
    assert dc.content == {}


def test_error_chunk_has_structured_fields() -> None:
    ec = error_chunk(message="boom", retryable=False, status_code=400)
    assert ec.type == "error"
    assert ec.content == {
        "message": "boom",
        "retryable": False,
        "status_code": 400,
    }


def test_model_chunk_is_frozen() -> None:
    c = text_delta("hi")
    with pytest.raises(ValidationError):
        c.type = "done"  # type: ignore[misc]


def test_model_chunk_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ModelChunk(type="done", content={}, other="nope")  # type: ignore[call-arg]


def test_sampling_enforces_bounds() -> None:
    Sampling(temperature=0.0)
    Sampling(temperature=2.0)
    with pytest.raises(ValidationError):
        Sampling(temperature=-0.1)
    with pytest.raises(ValidationError):
        Sampling(temperature=2.1)
    with pytest.raises(ValidationError):
        Sampling(top_p=1.5)
    with pytest.raises(ValidationError):
        Sampling(max_tokens=0)


def test_router_profile_defaults_compose() -> None:
    p = RouterProfile(model=ModelConfig(provider="anthropic", model_id="x"))
    assert p.caching.provider_strategy == "none"
    assert p.error_handling.max_retries == 3
    assert p.sampling.temperature == 0.7
    assert p.pricing is None


def test_router_profile_accepts_pricing_override() -> None:
    p = RouterProfile(
        model=ModelConfig(provider="anthropic", model_id="x"),
        pricing=Pricing(input_per_million=1.0, output_per_million=2.0),
    )
    assert p.pricing is not None
    assert p.pricing.input_per_million == 1.0


def test_caching_config_roundtrip() -> None:
    cc = CachingConfig(
        provider_strategy="explicit_breakpoints",
        breakpoints=[
            CachingBreakpoint(after="system_prompt"),
            CachingBreakpoint(after="compaction_summary"),
        ],
    )
    assert len(cc.breakpoints) == 2
    assert cc.breakpoints[0].after == "system_prompt"


def test_error_handling_defaults() -> None:
    eh = ErrorHandling()
    assert eh.retry_on_transient is True
    assert eh.max_retries == 3
    assert eh.backoff_base_seconds == 1.0
    assert eh.backoff_multiplier == 2.0
