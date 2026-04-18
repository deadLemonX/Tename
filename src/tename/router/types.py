"""Types for the Model Router.

The router consumes a narrow projection of the harness `Profile` schema
(`RouterProfile` + nested config models below). S6 builds the full profile
loader and validation; it is expected to reuse these models verbatim or
embed them unchanged. Additions to the full profile (context,
stop_conditions, tool_format, quirks) are not the router's concern.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---- Streaming chunk types --------------------------------------------------

ChunkType = Literal[
    "text_delta",
    "tool_call_start",
    "tool_call_delta",
    "tool_call_end",
    "usage",
    "done",
    "error",
]


class ModelChunk(BaseModel):
    """A single event in a model response stream.

    The `content` dict schema is per-`type`:
      - text_delta:     {"text": str}
      - tool_call_start:{"tool_id": str, "tool_name": str, "index": int}
      - tool_call_delta:{"tool_id": str, "partial_json": str, "index": int}
      - tool_call_end:  {"tool_id": str, "tool_name": str, "input": dict,
                         "index": int}
      - usage:          Usage fields as a dict (serialized Usage)
      - done:           {} (stream terminator)
      - error:          {"message": str, "retryable": bool,
                         "status_code": int | None}

    Factory helpers (text_delta(), tool_call_start(), ...) are the preferred
    way to construct chunks; they encode the schema above.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: ChunkType
    content: dict[str, Any] = Field(default_factory=dict)


def text_delta(text: str) -> ModelChunk:
    return ModelChunk(type="text_delta", content={"text": text})


def tool_call_start(*, tool_id: str, tool_name: str, index: int) -> ModelChunk:
    return ModelChunk(
        type="tool_call_start",
        content={"tool_id": tool_id, "tool_name": tool_name, "index": index},
    )


def tool_call_delta(*, tool_id: str, partial_json: str, index: int) -> ModelChunk:
    return ModelChunk(
        type="tool_call_delta",
        content={"tool_id": tool_id, "partial_json": partial_json, "index": index},
    )


def tool_call_end(
    *, tool_id: str, tool_name: str, tool_input: dict[str, Any], index: int
) -> ModelChunk:
    return ModelChunk(
        type="tool_call_end",
        content={
            "tool_id": tool_id,
            "tool_name": tool_name,
            "input": tool_input,
            "index": index,
        },
    )


def usage_chunk(usage: Usage) -> ModelChunk:
    return ModelChunk(type="usage", content=usage.model_dump())


def done_chunk() -> ModelChunk:
    return ModelChunk(type="done", content={})


def error_chunk(
    *, message: str, retryable: bool, status_code: int | None = None
) -> ModelChunk:
    return ModelChunk(
        type="error",
        content={
            "message": message,
            "retryable": retryable,
            "status_code": status_code,
        },
    )


# ---- Usage ------------------------------------------------------------------


class Usage(BaseModel):
    """Token usage for a single completion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = None


# ---- Messages and tools -----------------------------------------------------

Role = Literal["system", "user", "assistant", "tool"]


class ContentBlock(BaseModel):
    """A single content block inside a message.

    The `type` determines which other fields are populated. This mirrors the
    Anthropic content-block shape closely enough to pass through with minimal
    translation; the provider layer adapts to the exact wire format.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["text", "tool_use", "tool_result"]
    text: str | None = None
    # tool_use
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None
    # tool_result
    tool_use_id: str | None = None
    content: str | list[dict[str, Any]] | None = None
    is_error: bool | None = None


class Message(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Role
    content: str | list[ContentBlock]


class ToolDef(BaseModel):
    """JSONSchema-shaped tool definition. Provider-agnostic."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    input_schema: dict[str, Any]


# ---- Router-facing profile subset ------------------------------------------
#
# These are the profile fields the router actually reads at runtime. S6's
# ProfileLoader will build the full Profile (adding context, stop_conditions,
# tool_format, quirks) and is expected to reuse these exact models.

ProviderType = Literal["anthropic", "openai", "google", "openai_compatible"]
CachingStrategy = Literal[
    "explicit_breakpoints", "automatic_prefix", "explicit_api", "none"
]


class ModelConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: ProviderType
    model_id: str
    display_name: str | None = None
    description: str | None = None


class CachingBreakpoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    after: Literal["system_prompt", "compaction_summary"]


class CachingConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_strategy: CachingStrategy = "none"
    breakpoints: list[CachingBreakpoint] = Field(default_factory=list)
    cache_ttl_seconds: int = 300


class Sampling(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    max_tokens: int = Field(default=4096, gt=0)


class ErrorHandling(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    retry_on_transient: bool = True
    max_retries: int = Field(default=3, ge=0)
    backoff_base_seconds: float = Field(default=1.0, gt=0.0)
    backoff_multiplier: float = Field(default=2.0, ge=1.0)


class Pricing(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    input_per_million: float = Field(ge=0.0)
    output_per_million: float = Field(ge=0.0)
    cached_input_per_million: float | None = Field(default=None, ge=0.0)


class RouterProfile(BaseModel):
    """The slice of a full Profile the Model Router reads.

    Validation of cross-field rules (e.g. compaction threshold ordering) lives
    in S6's ProfileLoader; this model only enforces per-field bounds that the
    router relies on directly.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: ModelConfig
    caching: CachingConfig = Field(default_factory=CachingConfig)
    sampling: Sampling = Field(default_factory=Sampling)
    error_handling: ErrorHandling = Field(default_factory=ErrorHandling)
    pricing: Pricing | None = None


__all__ = [
    "CachingBreakpoint",
    "CachingConfig",
    "CachingStrategy",
    "ChunkType",
    "ContentBlock",
    "ErrorHandling",
    "Message",
    "ModelChunk",
    "ModelConfig",
    "Pricing",
    "ProviderType",
    "Role",
    "RouterProfile",
    "Sampling",
    "ToolDef",
    "Usage",
    "done_chunk",
    "error_chunk",
    "text_delta",
    "tool_call_delta",
    "tool_call_end",
    "tool_call_start",
    "usage_chunk",
]
