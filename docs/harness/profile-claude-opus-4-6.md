# Claude Opus 4.6 Profile (reference)

The reference profile that ships as the default in v0.1. All other profiles should be written in the same style.

## The YAML

```yaml
# profiles/claude-opus-4-6.yaml

model:
  provider: anthropic
  model_id: claude-opus-4-6
  display_name: "Claude Opus 4.6"
  description: |
    Anthropic's flagship model as of early 2026. Strong at agentic tasks,
    coding, and long-form reasoning. This is Tename's default profile.

context:
  max_tokens: 200000
  effective_budget: 160000
  compaction_threshold: 128000
  compaction_strategy: truncate
  keep_last_n_events: 20

caching:
  provider_strategy: explicit_breakpoints
  breakpoints:
    - after: system_prompt
    - after: compaction_summary
  cache_ttl_seconds: 300

tool_format: anthropic_tool_use

stop_conditions:
  no_tool_calls_for: 1
  max_turns: 50
  max_duration_seconds: 3600

error_handling:
  retry_on_transient: true
  max_retries: 3
  backoff_base_seconds: 1.0
  backoff_multiplier: 2.0

sampling:
  temperature: 0.7
  top_p: 1.0
  max_tokens: 8192

quirks: []  # Opus 4.6 has no known quirks as of profile creation

pricing:
  input_per_million: 15.00
  output_per_million: 75.00
  cached_input_per_million: 1.50
```

## Design rationale

### Why effective_budget = 80% of max_tokens

Claude's 200k context window is the hard ceiling. In practice, we reserve 20% for:
- System prompt and tool definitions (always present)
- Model's response tokens (can't be known in advance)
- Safety margin against provider-side reserved tokens

Using 100% of context leads to truncation errors from the provider. 80% is the empirical sweet spot.

### Why compaction_threshold = 80% of effective_budget

When context reaches 80% of the effective budget, we compact. We don't wait until we're at 100% because:
- Compaction itself takes time; we want room to maneuver
- Near-limit context hurts model performance (diluted attention)
- Some events will arrive during compaction; we need headroom

### Why explicit breakpoints

Anthropic's API supports explicit cache control via `cache_control` markers. We place them:

1. After the system prompt → everything from start to here is cached
2. After the most recent compaction summary → summary stays cached between turns

This achieves typically 60-80% cache hit rates on multi-turn sessions, which is a 70-80% cost reduction on cached tokens.

### Why no quirks (as of profile creation)

Opus 4.6 was released in late 2025 specifically to fix several quirks from earlier Claude versions. Current observations don't reveal systematic issues that need workarounds.

If a quirk is discovered, it will be added to this profile with documentation and a review date.

### Why temperature 0.7

Default for agentic work. Lower (0.3) for deterministic tasks like code generation. Higher (1.0) for creative tasks. Users can override per session.

## Benchmark results

Run against the v0.1 benchmark suite (5 tasks):

| Task | Result | Notes |
|------|--------|-------|
| research-001 | Pass | Thorough, well-sourced answer |
| coding-001 | Pass | Correct fix with test |
| data-001 | Pass | Accurate analysis with code |
| tool-001 | Pass | Selected correct tool |
| integration-001 | Pass | Maintained context across turns |

Overall: 5/5 pass rate. Profile is stable.

## Variants

### Research variant

For research tasks, lower temperature and use full context:

```yaml
# profiles/claude-opus-4-6-research.yaml
extends: claude-opus-4-6
description: "Variant tuned for research tasks."

sampling:
  temperature: 0.3
  max_tokens: 16384  # Allow longer responses

context:
  effective_budget: 180000  # Use more context
```

### Coding variant

For coding tasks, very low temperature:

```yaml
# profiles/claude-opus-4-6-coding.yaml
extends: claude-opus-4-6
description: "Variant tuned for coding tasks."

sampling:
  temperature: 0.2
  max_tokens: 16384
```

## When to update this profile

- New Opus version released → create new profile (don't modify this one)
- Known quirk discovered → add to quirks with review_date
- Pricing change → update pricing fields
- Provider API change requiring different caching → update caching strategy

Profile changes require benchmark validation. Don't merge profile changes that make benchmark scores worse.
