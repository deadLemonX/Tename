"""Unit tests for ModelRouter dispatch and usage enrichment."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest

from tename.router import ModelRouter
from tename.router.providers.base import ProviderInterface
from tename.router.types import (
    Message,
    ModelChunk,
    ModelConfig,
    Pricing,
    RouterProfile,
    ToolDef,
    Usage,
    done_chunk,
    text_delta,
    usage_chunk,
)


class _ScriptedProvider(ProviderInterface):
    def __init__(self, chunks: Sequence[ModelChunk]) -> None:
        self._chunks = list(chunks)
        self.calls: list[tuple[RouterProfile, list[Message], list[ToolDef] | None]] = []

    async def complete(
        self,
        profile: RouterProfile,
        messages: Sequence[Message],
        tools: Sequence[ToolDef] | None = None,
    ) -> AsyncIterator[ModelChunk]:
        self.calls.append((profile, list(messages), list(tools) if tools else None))
        for c in self._chunks:
            yield c


async def _collect(gen: AsyncIterator[Any]) -> list[Any]:
    return [c async for c in gen]


async def test_router_dispatches_to_provider_by_name() -> None:
    chunks = [text_delta("hi"), usage_chunk(Usage(input_tokens=5, output_tokens=2)), done_chunk()]
    provider = _ScriptedProvider(chunks)
    router = ModelRouter(providers={"anthropic": provider})

    profile = RouterProfile(
        model=ModelConfig(provider="anthropic", model_id="claude-opus-4-6")
    )
    out = await _collect(
        router.complete(profile, [Message(role="user", content="hi")])
    )

    assert len(provider.calls) == 1
    assert [c.type for c in out] == ["text_delta", "usage", "done"]


async def test_router_rejects_unknown_provider() -> None:
    router = ModelRouter(providers={"anthropic": _ScriptedProvider([])})
    profile = RouterProfile(
        model=ModelConfig(provider="openai", model_id="gpt-5"),
    )
    with pytest.raises(ValueError, match="no provider registered"):
        _ = await _collect(
            router.complete(profile, [Message(role="user", content="hi")])
        )


async def test_router_enriches_usage_with_cost_from_default_table() -> None:
    usage = Usage(input_tokens=1000, output_tokens=500, cached_input_tokens=200)
    provider = _ScriptedProvider([usage_chunk(usage), done_chunk()])
    router = ModelRouter(providers={"anthropic": provider})

    profile = RouterProfile(
        model=ModelConfig(provider="anthropic", model_id="claude-opus-4-6"),
    )
    out = await _collect(
        router.complete(profile, [Message(role="user", content="hi")])
    )
    u = next(c for c in out if c.type == "usage")
    assert u.content["cost_usd"] is not None
    assert u.content["cost_usd"] > 0
    # 800 uncached * 15/1M + 200 cached * 1.5/1M + 500 out * 75/1M
    expected = (800 * 15 + 200 * 1.5 + 500 * 75) / 1_000_000
    assert u.content["cost_usd"] == pytest.approx(expected)


async def test_router_honors_profile_pricing_override() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=0)
    provider = _ScriptedProvider([usage_chunk(usage), done_chunk()])
    router = ModelRouter(providers={"anthropic": provider})

    override = Pricing(input_per_million=1.0, output_per_million=2.0)
    profile = RouterProfile(
        model=ModelConfig(provider="anthropic", model_id="claude-opus-4-6"),
        pricing=override,
    )
    out = await _collect(
        router.complete(profile, [Message(role="user", content="hi")])
    )
    u = next(c for c in out if c.type == "usage")
    assert u.content["cost_usd"] == pytest.approx(1.0)


async def test_router_leaves_cost_none_when_no_pricing() -> None:
    usage = Usage(input_tokens=100, output_tokens=50)
    provider = _ScriptedProvider([usage_chunk(usage), done_chunk()])
    router = ModelRouter(providers={"anthropic": provider})
    profile = RouterProfile(
        model=ModelConfig(provider="anthropic", model_id="ghost-model"),
    )
    out = await _collect(
        router.complete(profile, [Message(role="user", content="hi")])
    )
    u = next(c for c in out if c.type == "usage")
    assert u.content["cost_usd"] is None


pytestmark = pytest.mark.asyncio
