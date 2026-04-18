"""ModelRouter: the Model Router's public API.

Dispatches a completion request to the right provider based on the profile
and enriches the `usage` chunk with `cost_usd` computed from the pricing
table (or the profile's inline pricing override).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence

from tename.router.pricing import compute_cost_usd, lookup_pricing
from tename.router.providers.anthropic import AnthropicProvider
from tename.router.providers.base import ProviderInterface
from tename.router.types import (
    Message,
    ModelChunk,
    RouterProfile,
    ToolDef,
    Usage,
    usage_chunk,
)

logger = logging.getLogger(__name__)


class ModelRouter:
    """Entry point for model completions.

    Construct with an optional mapping of provider name to `ProviderInterface`.
    In v0.1 the only built-in provider is Anthropic; tests can inject mocks
    here.
    """

    def __init__(self, providers: dict[str, ProviderInterface] | None = None) -> None:
        self._providers: dict[str, ProviderInterface] = providers or {
            "anthropic": AnthropicProvider(),
        }

    async def complete(
        self,
        profile: RouterProfile,
        messages: Sequence[Message],
        tools: Sequence[ToolDef] | None = None,
    ) -> AsyncIterator[ModelChunk]:
        provider_name = profile.model.provider
        provider = self._providers.get(provider_name)
        if provider is None:
            raise ValueError(
                f"no provider registered for '{provider_name}'. Known: {sorted(self._providers)}"
            )

        logger.debug(
            "routing completion",
            extra={
                "provider": provider_name,
                "model_id": profile.model.model_id,
                "num_messages": len(messages),
                "num_tools": len(tools) if tools else 0,
            },
        )

        async for chunk in provider.complete(profile, messages, tools):
            if chunk.type == "usage":
                yield self._enrich_usage(chunk, profile)
            else:
                yield chunk

    def _enrich_usage(self, chunk: ModelChunk, profile: RouterProfile) -> ModelChunk:
        pricing = lookup_pricing(
            provider=profile.model.provider,
            model_id=profile.model.model_id,
            override=profile.pricing,
        )
        usage = Usage.model_validate(chunk.content)
        cost = compute_cost_usd(usage, pricing)
        if cost is None:
            return chunk
        return usage_chunk(usage.model_copy(update={"cost_usd": cost}))


__all__ = ["ModelRouter"]
