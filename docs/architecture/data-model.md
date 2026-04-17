# Data Model

## Core types

### Session

```python
class Session:
    id: UUID
    agent_id: UUID
    status: Literal["active", "completed", "failed", "deleted"]
    created_at: datetime
    updated_at: datetime
    last_sequence: int
    metadata: dict  # User-supplied arbitrary context
    tenant_id: UUID = DEFAULT_TENANT  # Reserved for future multi-tenancy
```

### Event

```python
class Event:
    id: UUID  # Client-supplied for idempotency
    session_id: UUID
    sequence: int  # Monotonic within session
    type: EventType
    payload: dict  # Type-specific content
    created_at: datetime
```

### Agent

```python
class Agent:
    id: UUID
    name: str
    model: str  # Profile identifier, e.g. "claude-opus-4-6"
    framework: str  # Adapter identifier, e.g. "deep_agents"
    system_prompt: str
    tools: List[str]  # Tool identifiers
    sandbox_recipe: Optional[SandboxRecipe]
    created_at: datetime
```

## Event types

Every event has a type string. The payload schema depends on the type.

### `user_message`

User input to the agent.

```json
{
  "content": "Research the EV charging market",
  "attachments": []
}
```

### `assistant_message`

Model response to the user.

```json
{
  "content": "I'll research the EV charging market for you...",
  "is_complete": true,
  "usage": {
    "input_tokens": 1523,
    "output_tokens": 284,
    "cached_tokens": 1200
  }
}
```

During streaming, multiple `assistant_message` events are emitted with `is_complete: false` for partial content, then one final event with `is_complete: true`.

### `tool_call`

Model requests to execute a tool.

```json
{
  "id": "call_abc123",
  "tool_name": "web_search",
  "input": {"query": "EV charging market 2026"}
}
```

### `tool_result`

Result of a tool execution.

```json
{
  "call_id": "call_abc123",
  "success": true,
  "output": "Search results: ...",
  "error": null
}
```

If `success: false`, the `error` field contains the error details.

### `harness_event`

Events that represent harness-internal actions, not model or tool activity.

Types of harness events:
- `plan` — planning output from Deep Agents `write_todos`
- `compaction` — a compaction occurred, with summary
- `subagent_spawn` — a child session was spawned
- `subagent_result` — a child session completed
- `session_start` — session began
- `session_end` — session completed

```json
{
  "harness_event_type": "compaction",
  "strategy": "truncate",
  "dropped_events": 42,
  "summary": "Earlier research covered Tesla, ChargePoint, EVgo..."
}
```

### `system_event`

Events from Tename infrastructure (sandboxes, tool proxy, etc.).

```json
{
  "system_event_type": "sandbox_provisioned",
  "sandbox_id": "sbx_xyz789",
  "recipe_hash": "abc123"
}
```

### `error`

An error occurred somewhere.

```json
{
  "source": "model_router",
  "provider": "anthropic",
  "code": "rate_limit",
  "message": "Rate limit exceeded, retry after 30s"
}
```

## Relationships

```
Agent (1) ──< (N) Session (1) ──< (N) Event
```

An agent has many sessions. A session has many events. Events are ordered by sequence within a session.

## Schemas in Postgres

```sql
CREATE TABLE agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
    name TEXT NOT NULL,
    model TEXT NOT NULL,
    framework TEXT NOT NULL DEFAULT 'vanilla',
    system_prompt TEXT,
    tools JSONB DEFAULT '[]'::jsonb,
    sandbox_recipe JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
    agent_id UUID NOT NULL REFERENCES agents(id),
    status TEXT NOT NULL DEFAULT 'active',
    last_sequence INT NOT NULL DEFAULT 0,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE events (
    id UUID NOT NULL,
    session_id UUID NOT NULL REFERENCES sessions(id),
    sequence INT NOT NULL,
    type TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (session_id, id),
    UNIQUE (session_id, sequence)
);

CREATE INDEX idx_events_session_seq ON events (session_id, sequence);
CREATE INDEX idx_events_session_type_seq ON events (session_id, type, sequence);
CREATE INDEX idx_events_session_created ON events (session_id, created_at);
```

The `tenant_id` columns are reserved for future multi-tenancy but use a default value in v0.1. Adding multi-tenancy later means enabling RLS policies and passing real tenant_ids — no schema migration needed.

## Why JSONB for payloads

Events have many types with different schemas. We could create a table per type (strict typing), one big table with nullable columns (one table to rule them all), or JSONB (flexible schema).

JSONB wins because:
- Easy to add new event types without schema migrations
- Queries can still index on JSONB fields if needed
- The event type drives the payload schema, which is enforced in Python/Pydantic, not the database
- Postgres JSONB is performant enough for our use case

The trade-off is we don't get database-level validation of payload schemas. We rely on Pydantic models in Python for that.
