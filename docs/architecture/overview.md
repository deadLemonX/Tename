# Architecture Overview

## System diagram

```
            ┌────────────────┐
            │   Developer's  │
            │      Code      │  (uses the Python SDK)
            └───────┬────────┘
                    │
            ┌───────▼────────┐
            │   Python SDK   │
            └───────┬────────┘
                    │ (HTTP or in-process)
            ┌───────▼────────┐
            │   Harness      │  (stateless loop)
            │   Runtime      │
            └─┬─────┬──────┬─┘
              │     │      │
         ┌────▼─┐ ┌─▼────┐ │
         │Session│ │Model │ │
         │Service│ │Router│ │
         └────┬──┘ └──────┘ │
              │              │
         ┌────▼───┐  ┌───────▼──────┐
         │Postgres│  │   Sandbox    │
         │  +     │  │   (Docker)   │
         │  SQLite│  └──────────────┘
         └────────┘
```

All these components run as a single Python process in v0.1 (with Postgres and Docker as external dependencies). The architecture is designed so they can be split into separate services later if needed for scale, but for v0.1 they're modules within one process.

## Component responsibilities

### Session Service

**What it does:** Stores the durable, append-only event log for all agent runs. Source of truth for "what has happened so far."

**v0.1 implementation:** Python module using SQLAlchemy, backed by Postgres (production) or SQLite (local dev, single-user scenarios).

**Key operations:**
- `create_session(agent_id)` → session_id
- `emit_event(session_id, event)` → idempotent via event.id
- `get_events(session_id, start=None, end=None, types=None)` → list of events
- `wake(session_id)` → session metadata including last_sequence

**What it does NOT do in v0.1:**
- No tenant isolation or RLS (single-user mode)
- No user authentication (single-user mode)
- No access control (single-user mode)
- No S3 offloading of large events (hard limit of 256KB per event, rejects larger)

### Harness Runtime

**What it does:** The stateless loop that calls the model, interprets responses, routes tool calls, and emits events to the session log.

**v0.1 implementation:** Python module, single-threaded async loop per session. Profile loader reads YAML from `profiles/` directory.

**The core loop:**
```python
async def run_session(session_id: str):
    session = await session_svc.wake(session_id)
    profile = load_profile(session.model)
    adapter = get_adapter(session.framework)
    
    while True:
        events = await session_svc.get_events(session_id)
        context = adapter.build_context(events, profile)
        
        async for chunk in model_router.complete(profile.model, context):
            event = adapter.chunk_to_event(chunk)
            await session_svc.emit_event(session_id, event)
        
        if should_stop(events):
            break
            
        if needs_compaction(events, profile):
            await compact(session_id, profile)
```

**What it does NOT do:**
- No retries of failed model calls (that's the model router's job)
- No tool execution (delegated to sandbox or tool proxy)
- No credential handling (credentials are in the vault)

### Model Router

**What it does:** Routes model calls to the right provider, handles streaming, captures token usage.

**v0.1 implementation:** Python module wrapping LiteLLM for basic provider routing, with our own code for per-provider features (Anthropic cache breakpoints, Gemini explicit cache API).

**Supported providers in v0.1:**
- Anthropic (direct API)
- OpenAI (direct API)
- Any OpenAI-compatible endpoint (for self-hosted models)

**Added in v0.2:**
- Google (Gemini)
- Anthropic via Bedrock
- Anthropic via Vertex AI

**What it does NOT do:**
- No model selection logic (profile specifies the model)
- No cost tracking beyond usage capture (the SDK can display it)
- No fallback chains in v0.1 (added in v0.2 if users ask)

### Sandbox

**What it does:** Executes LLM-generated code in isolation. Bounded lifetime, bounded resources.

**v0.1 implementation:** Docker containers provisioned on-demand. Single backend (Docker). Interface designed to support additional backends later.

**Lifecycle:**
- Provisioned lazily (first tool call, not session start)
- Reused within a session (don't destroy and recreate)
- Destroyed when session completes or times out
- Resource limits: 2 CPUs, 4GB RAM, 10 minute default timeout

**What it does NOT do:**
- No pre-warmed pools in v0.1 (cold-start is acceptable for OSS users)
- No persistent filesystem across sessions
- No network egress restrictions by default (user configures if desired)

### Tool Proxy + Vault

**What it does:** Handles external tool calls that need credentials, so the sandbox never sees secrets.

**v0.1 implementation:** Python module. Vault is an encrypted file on disk (using Python `cryptography` library). Tool proxy is a function the harness calls, not a separate process yet.

**Supported tool types:**
- HTTP-based tools (web search, etc.) — proxy makes the call with credentials from vault
- MCP servers — proxy handles authentication transparently
- Sandbox tools (bash, python, file ops) — no credentials needed, runs in sandbox

**What it does NOT do in v0.1:**
- No external vault integration (AWS Secrets Manager comes later)
- No audit logging beyond standard Python logging
- No circuit breakers (added in v0.2 if needed)

### Python SDK

**What it does:** The library developers install (`pip install tename-sdk` — real name TBD) to use Tename from their code.

**v0.1 API:**
```python
from tename import Tename

# Initialize
client = Tename()

# Create an agent (reusable config)
agent = client.agents.create(
    name="researcher",
    model="claude-opus-4-6",
    system_prompt="You are a research assistant.",
    tools=["web_search", "python"]
)

# Start a session
session = client.sessions.create(agent_id=agent.id)

# Send a message and stream responses
for event in session.send("Research the EV charging market"):
    if event.type == "assistant_message":
        print(event.payload["content"], end="")

# Later, inspect the session
events = client.sessions.get_events(session.id)
```

**Two modes:**
- **In-process:** SDK and runtime run in the same Python process (simplest, for scripts)
- **Client-server:** SDK connects to a separately-running runtime via HTTP (for longer-running systems)

## How the pieces fit together

When a developer runs a session:

1. Developer code calls `session.send("...")` on the SDK
2. SDK emits a `user_message` event to the Session Service
3. SDK triggers the Harness Runtime to process the session
4. Harness wakes the session, reads events, loads the profile
5. Harness calls the Model Router with built context
6. Model Router calls the provider, streams response back
7. Harness emits `assistant_message` events (streaming) and `tool_call` events
8. For tool calls:
   - Sandbox tools → Sandbox executes, returns result → `tool_result` event
   - External tools → Tool Proxy handles with credentials → `tool_result` event
9. Harness loops, calling the model again with updated context
10. When the model stops requesting tools, the harness marks the session complete
11. SDK yields events to the developer's code as they're emitted

The whole flow from `session.send()` to final event is streaming. The developer sees progress in real time.

## Scaling beyond v0.1

The architecture is designed so components can be split into separate services if needed for scale:

**Multi-tenancy addition:** The Session Service already has a `tenant_id` column in its schema (unused in v0.1). Policies can be added without schema changes.

**Cloud sandbox:** The Sandbox interface supports multiple backends. Adding managed sandbox providers is a new module implementing the interface. No changes to the Harness.

**Hosted service:** The in-process mode and client-server mode are already both supported. A hosted service is just running the server mode in cloud infrastructure with auth in front.

**Enterprise features:** SSO, audit logs, advanced observability — these are layers that can be added without touching the core.

None of this work happens in v0.1. But v0.1's architecture doesn't block it.

## What's explicitly OUT of v0.1

To ship v0.1 in a reasonable timeframe as a side project, we explicitly exclude:

- Multi-tenancy, auth, API keys (single-user only)
- Hosted cloud service (local Docker only)
- Web UI for session replay (CLI and SDK only)
- Advanced compaction strategies (truncation only)
- Pre-warmed sandbox pools (cold start is fine)
- Multiple sandbox backends (Docker only)
- TypeScript SDK (Python only)
- Automated benchmark grading (manual grading for v0.1)
- SOC 2 or other compliance certifications
- Horizontal scaling of the runtime (single process is enough)

Each of these is a known deferred item. None of them are forgotten; they're just not v0.1 scope.

## Dependencies

**Required to run Tename:**
- Python 3.12+
- Docker (for sandboxes)
- Postgres 16+ (or SQLite for single-user)

**Required to develop against Tename:**
- An API key from at least one model provider
- The Python SDK (`pip install ...`)

That's it. No AWS account, no Kubernetes cluster, no special hardware.
