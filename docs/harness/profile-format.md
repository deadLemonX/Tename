# Profile Format

Profiles are YAML files in the `profiles/` directory. They encode all model-specific behavior.

## Full schema

```yaml
# profiles/claude-opus-4-6.yaml

# --- Model identity ---
model:
  provider: anthropic  # anthropic, openai, google, openai_compatible
  model_id: claude-opus-4-6  # The provider's model identifier
  display_name: "Claude Opus 4.6"  # For logs and UI
  description: "Anthropic's flagship model, tuned for agentic tasks."

# --- Context management ---
context:
  max_tokens: 200000  # Model's hard context limit
  effective_budget: 160000  # How much we actually use (80% is typical)
  compaction_threshold: 128000  # When to compact (80% of effective)
  compaction_strategy: truncate  # truncate only in v0.1
  keep_last_n_events: 20  # When truncating, always keep this many recent

# --- Caching ---
caching:
  provider_strategy: explicit_breakpoints  # explicit_breakpoints, automatic_prefix, explicit_api, none
  breakpoints:  # For explicit_breakpoints strategy
    - after: system_prompt
    - after: compaction_summary
  cache_ttl_seconds: 300  # Provider-dependent

# --- Tool format ---
tool_format: anthropic_tool_use  # anthropic_tool_use, openai_function_calling, json_schema

# --- Stop conditions ---
stop_conditions:
  no_tool_calls_for: 1  # Stop if no tool calls for N consecutive responses
  max_turns: 50  # Hard ceiling on loop iterations
  max_duration_seconds: 3600  # Stop after this much wall clock time

# --- Error handling ---
error_handling:
  retry_on_transient: true
  max_retries: 3
  backoff_base_seconds: 1.0
  backoff_multiplier: 2.0

# --- Sampling parameters ---
sampling:
  temperature: 0.7
  top_p: 1.0
  max_tokens: 4096  # Per-response, different from context budget

# --- Known quirks ---
quirks:
  - name: example_quirk
    added: 2026-01-15
    review_date: 2026-07-15
    description: "Brief description of the behavior and why mitigation is needed."
    mitigation: name_of_mitigation_function
    enabled: true
    notes: "Additional context for contributors."

# --- Pricing (for usage reporting) ---
pricing:
  input_per_million: 15.00
  output_per_million: 75.00
  cached_input_per_million: 1.50  # If provider supports it
```

## Required vs optional fields

**Required:**
- `model.provider`
- `model.model_id`
- `context.max_tokens`
- `context.effective_budget`
- `tool_format`

**Optional with defaults:**
- `context.compaction_threshold` → defaults to 80% of effective_budget
- `context.compaction_strategy` → defaults to "truncate"
- `context.keep_last_n_events` → defaults to 10
- `caching.provider_strategy` → defaults to "none"
- `stop_conditions.max_turns` → defaults to 50
- `stop_conditions.no_tool_calls_for` → defaults to 1
- `error_handling.*` → sensible defaults
- `sampling.*` → provider defaults
- `quirks` → empty list
- `pricing` → no pricing reported if absent

## Validation rules

Enforced at profile load time. Invalid profiles raise errors; they don't silently work.

1. `model.provider` must be one of: anthropic, openai, google, openai_compatible
2. `context.effective_budget` must be ≤ `context.max_tokens`
3. `context.compaction_threshold` must be < `context.effective_budget`
4. `context.compaction_strategy` must be one of: truncate, summarize, file_offload
   - v0.1 only supports truncate; others raise NotImplementedError
5. Every `quirks[].review_date` must be after `quirks[].added` date
6. Every `quirks[].mitigation` must reference a function that exists in the harness
7. `tool_format` must be one of the supported formats
8. `sampling.temperature` must be between 0 and 2
9. `sampling.top_p` must be between 0 and 1

## The quirks field

Quirks are the most important part of profiles. They're where per-model workarounds live. Every quirk must be documented with:

- **name:** A short identifier (for logs and tests)
- **added:** When the quirk was discovered (ISO date)
- **review_date:** When to check if it's still needed (typically 6 months later)
- **description:** Human-readable explanation of what the behavior is and why we compensate
- **mitigation:** Which harness function implements the workaround
- **enabled:** Whether the quirk is currently active
- **notes:** Additional context for contributors

### Why review_date?

This is the mechanism that prevents harness rot. Every quirk has a date when we should check if it's still needed. When the review date arrives:

1. Disable the quirk (`enabled: false`)
2. Run the benchmark suite
3. If benchmarks still pass → delete the quirk entirely
4. If benchmarks fail → re-enable and push review_date forward 6 months

This is done as a scheduled ritual. Quarterly pruning keeps the harness clean.

### Quirk example

```yaml
quirks:
  - name: premature_wrapup
    added: 2025-10-15
    review_date: 2026-04-15
    description: |
      Claude Sonnet 4.5 tends to wrap up tasks prematurely when it senses
      context filling up, even when significant work remains. The model
      starts producing "I've completed the task..." responses around 60% 
      context capacity.
    mitigation: reset_context_at_capacity
    enabled: true
    notes: |
      Seen consistently across research and coding tasks. Root cause appears
      to be training data. May be fixed in Opus 4.6. We should benchmark with
      this disabled once Opus 4.6 becomes our default.
```

## Profile inheritance

Profiles can extend other profiles via the `extends` field:

```yaml
# profiles/claude-opus-4-6-research.yaml
extends: claude-opus-4-6
description: "Variant tuned for research tasks."

# Override or add fields
sampling:
  temperature: 0.3  # Lower for more focused research
  
context:
  max_tokens: 200000
  effective_budget: 180000  # Use more context for research
```

Inherited values are overridden key-by-key. Nested dicts are merged, not replaced.

This lets users create workload-specific variants without copying the whole profile.

## Where profiles live

**Built-in profiles** ship with Tename in the repo's `profiles/` directory:
- `claude-opus-4-6.yaml`
- `claude-sonnet-4-6.yaml` (v0.2)
- `claude-haiku-4-5.yaml` (v0.2)
- `gpt-5.yaml` (v0.2)
- `gemini-3-pro.yaml` (v0.3)
- `llama-4-405b.yaml` (v0.3)

**User profiles** can live anywhere on disk. Users point Tename at their custom profiles via `Tename_PROFILES_DIR` env var or explicit configuration.

## Contributing a profile

To contribute a new model's profile:

1. Start from the closest existing profile
2. Adjust model identity fields
3. Tune context and caching for the new provider
4. Document any quirks you encounter
5. Run the benchmark suite against your profile
6. Submit a PR with benchmark results

The benchmark suite validates that your profile actually works well, not just that it's syntactically correct. PRs without benchmark results are incomplete.
