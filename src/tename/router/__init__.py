"""Model Router: provider dispatch, streaming, usage capture.

Public API:

    from tename.router import ModelRouter, RouterProfile, Message, ModelChunk

Populated in S5. See docs/architecture/model-router.md.
"""

from tename.router.pricing import compute_cost_usd, lookup_pricing
from tename.router.providers.anthropic import AnthropicProvider
from tename.router.providers.base import ProviderInterface
from tename.router.service import ModelRouter
from tename.router.types import (
    CachingBreakpoint,
    CachingConfig,
    CachingStrategy,
    ChunkType,
    ContentBlock,
    ErrorHandling,
    Message,
    ModelChunk,
    ModelConfig,
    Pricing,
    ProviderType,
    Role,
    RouterProfile,
    Sampling,
    ToolDef,
    Usage,
    done_chunk,
    error_chunk,
    text_delta,
    tool_call_delta,
    tool_call_end,
    tool_call_start,
    usage_chunk,
)

__all__ = [
    "AnthropicProvider",
    "CachingBreakpoint",
    "CachingConfig",
    "CachingStrategy",
    "ChunkType",
    "ContentBlock",
    "ErrorHandling",
    "Message",
    "ModelChunk",
    "ModelConfig",
    "ModelRouter",
    "Pricing",
    "ProviderInterface",
    "ProviderType",
    "Role",
    "RouterProfile",
    "Sampling",
    "ToolDef",
    "Usage",
    "compute_cost_usd",
    "done_chunk",
    "error_chunk",
    "lookup_pricing",
    "text_delta",
    "tool_call_delta",
    "tool_call_end",
    "tool_call_start",
    "usage_chunk",
]
