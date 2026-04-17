# Model Router

## Purpose

Abstracts model provider APIs. Takes a profile and request, routes to the correct provider, handles streaming and usage tracking.

## Design

Thin layer built on LiteLLM for basic provider routing, with our own code for per-provider features LiteLLM doesn't handle (Anthropic cache breakpoints, Gemini explicit cache API).

## API

```python
class ModelRouter:
    async def complete(
        profile: Profile,
        messages: List[Message],
        tools: Optional[List[ToolDef]] = None,
    ) -> AsyncIterator[ModelChunk]:
        """Stream a model completion."""
```

## Supported providers in v0.1

- **Anthropic** (direct API via `anthropic` Python SDK)
- **OpenAI** (direct API via `openai` Python SDK)
- **OpenAI-compatible endpoints** (for self-hosted models, local Ollama, etc.)

Added in v0.2:
- Google (Gemini)
- Anthropic via Bedrock
- Anthropic via Vertex AI

## Streaming chunk types

```python
class ModelChunk:
    type: Literal[
        "text_delta",
        "tool_call_start",
        "tool_call_delta",
        "tool_call_end",
        "usage",
        "done",
        "error"
    ]
    content: Any  # Type-specific payload
```

- `text_delta`: partial text from the model
- `tool_call_start`: model is beginning a tool call, includes tool_id
- `tool_call_delta`: incremental tool call arguments
- `tool_call_end`: tool call complete, includes final arguments
- `usage`: token usage (typically at end of stream)
- `done`: stream complete
- `error`: something went wrong, stream ending

## Per-provider handling

### Anthropic

- Uses `anthropic` Python SDK
- Places `cache_control` markers where the profile specifies
- Captures usage from final message (input_tokens, output_tokens, cached_tokens)
- Handles Anthropic-specific tool use format

### OpenAI

- Uses `openai` Python SDK
- No explicit caching (OpenAI caches prefixes automatically)
- Captures usage from `usage` field
- Handles OpenAI function calling format

### OpenAI-compatible endpoints

- Same as OpenAI but with custom `base_url`
- Used for Ollama, vLLM, LM Studio, etc.
- Caching depends on backend (some support prefix caching, others don't)
- Profile can disable caching if unsupported

## Usage tracking

Every completion captures token usage:

```python
class Usage:
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0  # Provider-specific; 0 if not applicable
    reasoning_tokens: int = 0  # For models with extended thinking
    cost_usd: Optional[float] = None  # Calculated from pricing.yaml
```

Usage is emitted as a chunk at the end of the stream. The SDK can display it.

## Pricing

Provider pricing lives in `pricing.yaml`:

```yaml
anthropic:
  claude-opus-4-6:
    input_per_million: 15.00
    output_per_million: 75.00
    cached_input_per_million: 1.50  # 10% of uncached
  claude-sonnet-4-6:
    input_per_million: 3.00
    output_per_million: 15.00
    cached_input_per_million: 0.30

openai:
  gpt-5:
    input_per_million: 20.00  # Hypothetical
    output_per_million: 80.00
```

Users can override pricing by providing their own `pricing.yaml`.

## Retries and error handling

v0.1 keeps this simple:
- Retry transient errors (5xx, network errors) 3 times with exponential backoff
- Do NOT retry on 4xx (bad request, auth error, quota exceeded)
- On final failure, yield `error` chunk and end stream
- Harness records the error as an event

v0.2 may add:
- Fallback chains (try Claude Opus, fall back to Sonnet, fall back to GPT)
- Provider-specific retry strategies
- Circuit breakers

## Testing requirements

- Unit tests with mocked provider responses
- Integration test against real Anthropic API (gated behind env var)
- Integration test against real OpenAI API (gated behind env var)
- Streaming test: verify chunks arrive in correct order
- Error handling test: provider returns 500, verify retry behavior
- Usage capture test: verify tokens are reported correctly
