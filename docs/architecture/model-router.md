# Model Router

## Purpose

Abstracts model provider APIs. Takes a profile and request, routes to the correct provider, handles streaming and usage tracking.

## Design

A `ProviderInterface` abstract base class with one `complete(profile,
messages, tools) -> AsyncIterator[ModelChunk]` contract. Each
provider module implements the ABC and owns its own SDK lifecycle
plus provider-specific quirks (Anthropic cache breakpoints, Gemini
explicit cache API once we add it). `litellm` is in the dependency
tree for later use but the v0.1 path calls the Anthropic SDK
directly.

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

- **Anthropic** (direct API via `anthropic` Python SDK).

v0.1 ships the Anthropic provider only. Adding a new provider = a new
file under `src/tename/router/providers/` implementing the ABC plus
pricing entries under `pricing.yaml`. Planned for v0.2+:

- OpenAI (direct API)
- OpenAI-compatible endpoints (Ollama, vLLM, LM Studio, self-hosted)
- Google (Gemini)
- Anthropic via Bedrock / Vertex AI

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

### Anthropic (v0.1)

- Uses `anthropic.AsyncAnthropic` with `client.messages.stream(...)`
- Places `cache_control` markers where the profile's caching config
  specifies (v0.1 supports `explicit_breakpoints` with an `after:
  system_prompt` hook; `after: compaction_summary` is a no-op until
  adapters emit compaction-summary messages)
- Captures usage from `message_delta` events (input, output, cached,
  reasoning tokens)
- Translates our `Message` / `ContentBlock` / `ToolDef` to Anthropic's
  wire format: system messages extract to the top-level `system`
  param; tool-role messages fold back into user messages with
  `tool_result` blocks
- Temperature and `top_p` are mutually exclusive per Opus 4.6's API
  constraint — the provider forwards `temperature` by default and
  switches to `top_p` (dropping `temperature`) only when the profile
  narrows `top_p < 1.0`
- Retries wrap manager `__aenter__` only. Once any chunk has been
  yielded, mid-stream failures terminate with an `error` chunk — no
  retry, since already-yielded chunks can't be replayed

### Planned: OpenAI / OpenAI-compatible / Gemini / Bedrock

Not shipped in v0.1. Each follows the same pattern:

1. A `providers/<name>.py` implementing the ABC.
2. Any provider-specific adapter for tool format (OpenAI function
   calling, Gemini tool specs, etc.).
3. Caching strategy per provider (OpenAI caches prefixes
   automatically; Gemini has an explicit cache API; OpenAI-compatible
   endpoints vary — the profile's `caching.provider_strategy` picks
   the right one).
4. Pricing entries in `pricing.yaml`.

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

Provider pricing lives in `src/tename/router/pricing.yaml`, bundled
in the wheel and read via `importlib.resources`. v0.1 only has
`anthropic.claude-opus-4-6` populated; additional entries arrive as
new providers/profiles do:

```yaml
anthropic:
  claude-opus-4-6:
    input_per_million: 15.00
    output_per_million: 75.00
    cached_input_per_million: 1.50  # 10% of uncached
```

A profile can override pricing via its own `pricing` block; the
bundled table is only a fallback. Unknown model/provider combinations
return `cost_usd = None` rather than crashing.

## Retries and error handling

v0.1 keeps this simple:
- Retry startup errors (5xx, network, timeout) up to
  `error_handling.max_retries` times with exponential backoff
  (`backoff_base_seconds * multiplier^attempt`)
- Do NOT retry on 4xx (bad request, auth error, quota exceeded) —
  yield a non-retryable `error` chunk immediately
- **Retries wrap `manager.__aenter__` only.** Once any chunk has been
  yielded, a mid-stream failure terminates with an `error` chunk and
  no retry; replaying already-yielded chunks would double-emit events
- Harness records the error as an event

v0.2 may add:
- Fallback chains (try Opus, fall back to Sonnet, fall back to GPT)
- Provider-specific retry strategies
- Circuit breakers

## Testing

Shipped coverage:

- Unit tests with mocked provider responses via stand-in
  `FakeAnthropic` / `FakeMessages` fixtures
  (`tests/router/conftest.py`)
- Streaming order tests, tool_use translation, usage capture
- Retry tests: 500-then-success, 400-no-retry, retries-exhausted,
  mid-stream error
- Integration test against real Anthropic API
  (`tests/router/test_anthropic_integration.py`, gated by
  `ANTHROPIC_API_KEY` + the `anthropic` pytest marker)
