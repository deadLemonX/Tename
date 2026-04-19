# Harness Runtime

## Purpose

The stateless loop that calls the model, interprets responses, routes tool calls, and emits events. The "brain" of the three-interface architecture.

## Core principle: statelessness

The harness holds NO state that survives a crash. All state lives in the session log. Every loop iteration rebuilds context from events. Killing the harness at any point is safe because a new instance can resume from the log.

This is non-negotiable. Any in-memory caching or buffering that persists across iterations violates the principle.

## The core loop

```python
async def run_session(session_id: str):
    # Wake up — get metadata
    session = await session_svc.wake(session_id)
    
    # Load profile for the model
    profile = load_profile(session.agent.model)
    
    # Load framework adapter
    adapter = get_adapter(session.agent.framework)
    
    max_turns = profile.max_turns or 50
    turn = 0
    
    while turn < max_turns:
        turn += 1
        
        # Rebuild context from ALL events in the session
        events = await session_svc.get_events(session_id)
        context = adapter.build_context(events, profile)
        
        # Call model with context
        async for chunk in model_router.complete(profile, context):
            event = adapter.chunk_to_event(chunk)
            await session_svc.emit_event(session_id, event)
        
        # Get the latest events to check for tool calls
        recent_events = await session_svc.get_events(
            session_id, 
            start=previous_sequence + 1
        )
        
        tool_calls = [e for e in recent_events if e.type == "tool_call"]
        
        if not tool_calls:
            # Model is done — no more tool calls to execute
            break
        
        # Execute tool calls
        for call in tool_calls:
            result = await execute_tool(call, session_id, profile)
            await session_svc.emit_event(session_id, result)
        
        # Check for compaction
        if await should_compact(events, profile):
            await compact_session(session_id, profile)
    
    # Mark session as completed
    await session_svc.mark_complete(session_id)
```

That's essentially the whole thing. Everything else is delegation to components.

## Framework adapters

The core loop is framework-agnostic. Adapters translate between
specific agent frameworks (Deep Agents, plus planned future adapters
like the Claude Agent SDK) and Tename primitives.

### Adapter interface

```python
class FrameworkAdapter(ABC):
    @abstractmethod
    def build_context(self, events: List[Event], profile: Profile) -> Context:
        """Convert session events into the framework's expected message format."""
    
    @abstractmethod
    def chunk_to_event(self, chunk: ModelChunk) -> Event:
        """Convert a model streaming chunk to a Tename event."""
    
    @abstractmethod
    def get_tools(self, agent_config: AgentConfig) -> List[ToolDef]:
        """Return tool definitions in the framework's format."""
    
    @abstractmethod
    def supports_streaming(self) -> bool:
        return True
```

### v0.1 adapters

**Deep Agents adapter** (`framework: deep_agents`):
- Folds the event log into Anthropic-canonical message shape
  (assistant message carries text + tool_use blocks together;
  tool_result events become tool-role messages with matching
  `tool_use_id`)
- Surfaces the Deep Agents built-in tool schemas (`write_todos`,
  `ls`, `read_file`, `write_file`, `edit_file`, `task`) as `ToolDef`s
  without importing the `deepagents` runtime
- Concept mapping documented in
  [`docs/harness/adapter-deep-agents.md`](../harness/adapter-deep-agents.md)

**Vanilla adapter** (`framework: vanilla`, fallback):
- Simple message-based interface for code that doesn't use a
  supported framework
- Carries tool rounds through context using the same grouping logic
  as the Deep Agents adapter, so multi-turn tool-using agents work
  out of the box
- Surfaces sandbox built-ins (`bash`, `python`, `file_*`) and
  registered proxy tools (e.g. `web_search`) per the agent's
  `tools` list

### Planned for v0.2+

- Claude Agent SDK
- Pydantic AI
- AutoGen (if demand exists)
- LangGraph (compatibility mode)

## Profile loading

Profiles are YAML files in a `profiles/` directory. The harness loads them at session start.

### Profile structure

```yaml
# profiles/claude-opus-4-6.yaml
model:
  provider: anthropic
  model_id: claude-opus-4-6
  
context:
  max_tokens: 200000
  effective_budget: 160000  # 80% of max
  compaction_threshold: 128000  # 80% of effective
  compaction_strategy: truncate  # or summarize, file_offload
  
caching:
  provider_strategy: explicit_breakpoints
  breakpoints:
    - after: system_prompt
    - after: compaction_summary
  
tool_format: anthropic_tool_use
  
stop_conditions:
  - no_tool_calls_for: 1
  - max_turns: 50
  
quirks:
  - name: context_anxiety
    added: 2025-10-01
    review_date: 2026-06-01
    description: "Claude Sonnet 4.5 wraps up tasks prematurely as context fills. Mitigation: reset context at 70% capacity."
    mitigation: reset_context_at_capacity
    enabled: false  # Not applicable to Opus 4.6, kept for reference
```

### Validation rules

- `effective_budget` must be ≤ `max_tokens`
- `compaction_threshold` must be < `effective_budget`
- Every quirk must have `added` and `review_date`
- `provider` must be a supported provider
- `model_id` must be a known model identifier
- Invalid profiles are rejected at load time with clear error messages

## Tool execution routing

Tool calls are routed based on tool type, defined in the agent config:

**Sandbox tools** (code execution):
- `bash`, `python`, `file_edit`, `file_read`, etc.
- Executed in the sandbox via Sandbox module
- Sandbox provisioned lazily on first tool call
- Sandbox ID stored in a `system_event` in the session

**Proxy tools** (external APIs):
- `web_search`, MCP servers, custom HTTP tools
- Executed via Tool Proxy with credentials from vault
- Credentials never visible to the model or sandbox

**Routing decision:**
```python
async def execute_tool(call, session_id, profile):
    tool_def = get_tool_def(call.tool_name)
    
    if tool_def.type == "sandbox":
        sandbox_id = await get_or_provision_sandbox(session_id)
        result = await sandbox.execute(sandbox_id, call.tool_name, call.input)
    elif tool_def.type == "proxy":
        result = await tool_proxy.execute(call.tool_name, call.input, session_id)
    else:
        raise ValueError(f"Unknown tool type: {tool_def.type}")
    
    return Event(
        id=new_id(),
        session_id=session_id,
        type="tool_result",
        payload={"call_id": call.id, "result": result}
    )
```

## Error handling

Errors are recorded as events, not exceptions that crash the harness:

- Model errors → `error` event with provider, code, message
- Tool errors → `tool_result` event with error field populated
- Sandbox failures → `system_event` noting the failure + `error` event
- Timeout → `error` event, then stop

The model sees tool errors as tool results (with error content) and can decide how to respond. The harness doesn't try to recover from errors beyond logging them — that's the model's job.

## Compaction strategy for v0.1

Compaction reduces active context when it approaches budget. v0.1 supports one strategy:

**Truncate:** Drop the oldest events, keeping:
- The original user message (always)
- The last N events (configured in profile)
- All compaction summary events (they're cheaper than original events)

A `harness_event` with type=compaction is emitted to record what was dropped. The original events stay in the log forever — they're just not in the active context.

**Not in v0.1:**
- Summarize strategy (requires extra model call, complexity)
- File offload strategy (requires sandbox filesystem access)
- Hybrid strategies

These come in v0.2 if users ask for them.

## Observability

Every loop iteration emits structured logs:
- session_id
- turn number
- model called
- tokens used (input and output)
- tool calls made
- event sequence numbers emitted

No distributed tracing in v0.1 (no multi-service architecture). Standard Python `logging` module with structured output (JSON).

## Testing requirements

- Unit tests for profile loading and validation
- Integration test: end-to-end session with mocked model, verify event emission
- Integration test: real API call to Anthropic, verify streaming works
- Crash recovery test: kill harness mid-stream, verify resume produces consistent state
- Max turns test: runaway agent is stopped
- Adapter tests: each adapter produces correct event types for known inputs
