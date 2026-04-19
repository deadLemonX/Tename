# Observability

## What Tename logs

Every significant operation emits structured JSON logs via Python's `logging` module.

### Log contexts

Every log entry includes:
- `timestamp` (ISO 8601)
- `level` (DEBUG, INFO, WARNING, ERROR)
- `logger` (the module that emitted it)
- `message` (human-readable)
- Context fields when applicable:
  - `session_id` - the session being operated on
  - `agent_id` - the agent being used
  - `event_id` - event being emitted
  - `sequence` - event sequence number
  - `model_id` - model being called
  - `tool_name` - tool being executed
  - `sandbox_id` - sandbox being used

### What we log at each level

**DEBUG:** Everything. Full event contents (unless sensitive), full request/response bodies, internal state transitions.

**INFO:** Significant events. Session created, session completed, event emitted (just the type and sequence), model call started, model call completed, sandbox provisioned.

**WARNING:** Unusual but recoverable. Retryable error on first attempt, approaching context limit, slow response from provider.

**ERROR:** Actual failures. Model call failed after retries, sandbox failed, database connection lost, credential not found.

### What we DON'T log at any level

- Credential values (ever)
- Full event payloads at INFO+ (too much noise; use DEBUG)
- User-provided content verbatim at WARNING+ (privacy concern)

## Viewing logs

In development:

```bash
# Tename logs to stdout by default
# During dev, tail them:
python your_agent.py 2>&1 | jq .

# Or filter by level:
python your_agent.py 2>&1 | jq 'select(.level == "ERROR")'
```

In production, forward stdout to your logging pipeline (Loki, ELK, CloudWatch, Datadog, whatever you use).

## Metrics

v0.1 doesn't expose a Prometheus endpoint or similar. Metrics you care about can be derived from logs:

- Session rate: count of "session_created" events over time
- Event rate: count of "event_emitted" events over time
- Model latency: durations between "model_call_started" and "model_call_completed"
- Error rate: count of ERROR-level logs

If operators want native metrics, that's a v0.2+ addition.

## Debugging sessions

The most powerful debugging tool is the session log itself. Every action the agent took, every model response, every tool call - it's all there in order.

```python
import asyncio
from uuid import UUID

from tename.sessions import EventType, SessionService


async def replay(database_url: str, session_id: UUID) -> None:
    service = SessionService(database_url)
    try:
        events = await service.get_events(session_id)
        for e in events:
            print(f"[{e.sequence}] {e.type.value}")
            if e.type == EventType.ASSISTANT_MESSAGE and e.payload.get("is_complete"):
                print(f"  content: {e.payload.get('content', '')[:100]}...")
            elif e.type == EventType.TOOL_CALL:
                print(f"  tool: {e.payload.get('tool_name')}")
                print(f"  input: {e.payload.get('input')}")
            elif e.type == EventType.TOOL_RESULT:
                is_err = e.payload.get("is_error", False)
                print(f"  is_error: {is_err}")
                if is_err:
                    print(f"  error: {e.payload.get('error')}")
    finally:
        await service.close()


asyncio.run(replay("postgresql+psycopg://...", UUID("...")))
```

A dedicated `tename sessions replay` CLI command is planned for v0.2;
in v0.1 the SessionService read path is the primary replay tool.
The `examples/deep_agents_research/main.py` script shows a similar
pretty-printer pattern.

## What's not in v0.1

- OpenTelemetry tracing
- Prometheus metrics endpoint
- Web UI for session inspection
- Alerting integrations
- Performance profiling helpers

These come in v0.2+ as operators request them. For v0.1, structured logs and the session log are sufficient.

## Future improvements

As Tename matures, observability will expand:

- Full OpenTelemetry support for tracing across session/harness/sandbox/model
- Native Prometheus metrics with meaningful SLI/SLO definitions
- Session replay UI as a flagship feature
- Customer-facing observability (letting your users of Tename-powered products debug their own agents)

None of this happens in v0.1. It's the commercial path, not the OSS path.
