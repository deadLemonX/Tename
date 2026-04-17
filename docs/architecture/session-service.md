# Session Service

## Purpose

Durable, append-only event log that stores all agent run state. The source of truth for "what has happened so far."

## Design

The Session Service is a Python module with a clean API. In v0.1 it runs in-process with the rest of Tename. The API is designed so it could be split into a separate service later if commercial deployment requires it.

### Two tables

**`sessions`**
- `id` UUID primary key
- `agent_id` UUID (references the agent config)
- `status` enum: active, completed, failed, deleted
- `created_at`, `updated_at` timestamps
- `last_sequence` int (highest event sequence number in this session)
- `metadata` JSONB (arbitrary session context)

**`events`**
- `id` UUID primary key (client-supplied for idempotency)
- `session_id` UUID (references sessions.id)
- `sequence` int (monotonic within a session)
- `type` string (event type, see data-model.md)
- `payload` JSONB (event-specific content)
- `created_at` timestamp
- UNIQUE (session_id, sequence)
- UNIQUE (session_id, id) — enforces idempotency

### Indexes

- `events(session_id, sequence)` — for chronological reads
- `events(session_id, type, sequence)` — for filtered reads
- `events(session_id, created_at)` — for time-range queries

## API

### `create_session(agent_id, metadata=None) -> Session`

Creates a new session with status='active'. Returns Session object.

**Idempotency:** If a `request_id` is passed in metadata and a session with that request_id already exists, return the existing session.

### `emit_event(session_id, event) -> Event`

Appends an event to the session log.

**Behavior:**
1. Acquire advisory lock on session_id
2. Read sessions.last_sequence
3. If event.id already exists for this session → return existing event (idempotent)
4. Insert event with sequence = last_sequence + 1
5. Update sessions.last_sequence
6. Release lock

**Large payloads:** Events over 256KB are rejected with an error in v0.1. Future versions may offload to S3 or filesystem.

### `get_events(session_id, start=None, end=None, types=None, limit=1000) -> List[Event]`

Reads events from a session.

**Parameters:**
- `start`: inclusive start sequence (default: 1)
- `end`: inclusive end sequence (default: last_sequence)
- `types`: filter to specific event types (default: all)
- `limit`: max events to return (default 1000, max 10000)

**Performance target:** 100 recent events from a session with 10k events returns in under 50ms.

### `wake(session_id) -> SessionInfo`

Gets session metadata for a harness instance about to resume work.

**Returns:** session_id, last_sequence, status, agent_id, metadata

**Raises:** NotFoundError if session doesn't exist. FailedPreconditionError if session is in terminal state.

**Does NOT acquire locks.** In v0.1 there's one harness per session; coordination isn't needed. If multi-instance becomes a requirement later, add a lease mechanism.

## Sequence number generation

Sequence numbers are monotonic per session (1, 2, 3, ...). They're assigned by the database under an advisory lock to guarantee no gaps and no duplicates even with concurrent writers.

**In Postgres:**
```sql
-- Inside a transaction
SELECT pg_advisory_xact_lock(hashtext(session_id::text));
SELECT last_sequence FROM sessions WHERE id = session_id;
-- next_sequence = last_sequence + 1
INSERT INTO events (...) VALUES (..., next_sequence, ...);
UPDATE sessions SET last_sequence = next_sequence WHERE id = session_id;
-- lock auto-releases at transaction end
```

**In SQLite (single-writer mode):**
- No advisory lock needed; SQLite serializes writes
- Same logic without explicit locking

## Idempotency guarantees

Every write accepts a client-supplied ID. Duplicate submissions with the same ID return the existing record without error.

This is what lets the harness safely retry after a crash. When the harness emits event_id=abc123 and then crashes mid-request, on restart it can safely re-emit event_id=abc123. The session service recognizes the ID and returns the existing event. No duplicates, no errors.

## What's different from the commercial version

The commercial v1 plan included:
- Multi-tenancy via Postgres RLS (removed in v0.1)
- `tenant_id` in every query (reserved in schema but unused in v0.1)
- Tenant isolation testing (not needed in v0.1)
- S3 offloading for large payloads (deferred)
- Replication and backup tooling (user's responsibility in v0.1)

The schema includes a `tenant_id` column with a default value. This means adding multi-tenancy later doesn't require a schema migration — just enable the feature and populate the column.

## Testing requirements

- Unit tests for each API method
- Integration tests that exercise the full lifecycle (create, emit many events, read, complete)
- Concurrency test: 10 async writers to the same session, verify no gaps or duplicates in sequences
- Idempotency test: same event_id submitted 100 times, verify single row
- Crash recovery test: write 50 events, kill process mid-write, restart, verify state is consistent
- Performance test: 1000 events/sec sustained for 60 seconds
