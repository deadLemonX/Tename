# Python SDK Design

The Python SDK is the developer-facing surface of Tename. This doc
describes the API that actually ships in v0.1. For a guided
clone → run walkthrough, see [QUICKSTART.md](../QUICKSTART.md); for
the reference install and config flow, see
[installation.md](installation.md).

## Design goals

1. **Natural Python.** Feels like a normal Python library.
2. **Obvious first use.** `Tename()` with env vars set should work.
3. **Streaming feels easy.** Iterating over events is the default
   pattern — no callbacks or magic context managers.
4. **Typed throughout.** IDE autocomplete just works. `pyright
   strict` is happy.
5. **Error messages teach.** Every error tells the user what to do
   next.

## Hello-world

```python
from tename import Tename

with Tename() as client:
    agent = client.agents.create(
        name="assistant",
        model="claude-opus-4-6",
        system_prompt="You are a helpful assistant.",
    )
    session = client.sessions.create(agent_id=agent.id)

    for event in session.send("What's 2 + 2?"):
        if event.type == "assistant_message" and event.payload.get("is_complete"):
            print(event.payload["content"])
```

Zero to a working agent in ~10 lines. `Tename()` reads
`TENAME_DATABASE_URL`, `ANTHROPIC_API_KEY`, and
`TENAME_VAULT_PASSPHRASE` from env; any can be overridden by passing
kwargs.

## Top-level client

### `Tename` (sync) and `AsyncTename`

```python
class AsyncTename:
    def __init__(
        self,
        database_url: str | None = None,        # env TENAME_DATABASE_URL (required)
        *,
        anthropic_api_key: str | None = None,   # env ANTHROPIC_API_KEY
        profiles_dir: str | None = None,        # env TENAME_PROFILES_DIR
        vault_path: str | None = None,          # default ~/.tename/vault.json.enc
        vault_passphrase: str | None = None,    # env TENAME_VAULT_PASSPHRASE
        enable_sandbox: bool = True,            # wire a DockerBackend sandbox
    ) -> None: ...

    agents:   AsyncAgentsClient      # create / get / list / delete
    sessions: AsyncSessionsClient    # create / get  (returns AsyncSessionHandle)
    vault:    AsyncVaultClient       # set / get / remove / list

    async def close(self) -> None: ...
    async def __aenter__(self) -> "AsyncTename": ...
    async def __aexit__(self, *exc) -> None: ...


class Tename:
    """Sync wrapper around AsyncTename on a dedicated background loop.

    Every method forwards to the async version via
    `asyncio.run_coroutine_threadsafe`. `session.send(...)` returns a
    plain sync iterator even though the harness runs asynchronously
    underneath.
    """
    # Same constructor kwargs as AsyncTename.
    agents:   SyncAgentsClient
    sessions: SyncSessionsClient
    vault:    AsyncVaultClient       # vault ops are thread-safe already

    def close(self) -> None: ...
    def __enter__(self) -> "Tename": ...
    def __exit__(self, *exc) -> None: ...
```

`Tename` and `AsyncTename` share the same internal services — both
own one `SessionService`, one `ModelRouter`, one optional
`Sandbox(DockerBackend)`, one `Vault`, one `ToolProxy`, one
`HarnessRuntime`. You pick one or the other based on whether your
caller is sync or async; you don't mix them in the same process.

Both expose `install_test_model_router(router)` as a public testing
seam — swaps the internal router + rebuilds the harness. Production
code must not call it.

### Not on the client in v0.1

- `client.profiles` — profile selection happens via the agent's
  `model` field; custom profiles are picked up from
  `TENAME_PROFILES_DIR` automatically.
- Any client-server transport — v0.1 runs in-process only.

## Sub-clients

### AgentsClient

```python
class AsyncAgentsClient:
    async def create(
        self,
        *,
        name: str,
        model: str,
        system_prompt: str | None = None,
        tools: list[str] | None = None,
        framework: str = "vanilla",
        sandbox_recipe: SandboxRecipe | None = None,
    ) -> Agent: ...

    async def get(self, agent_id: UUID) -> Agent: ...     # raises NotFoundError
    async def list(self) -> list[Agent]: ...              # ordered by created_at DESC
    async def delete(self, agent_id: UUID) -> None: ...   # raises NotFoundError
```

Sync mirror: `SyncAgentsClient` with the same methods, non-async.

All constructor args to `create` are keyword-only. `framework` is
`"vanilla"` or `"deep_agents"` in v0.1. `tools` is a list of tool
names — either sandbox built-ins (`bash`, `python`, `file_read`,
`file_write`, `file_edit`, `file_list`) or registered proxy tool
names (`web_search` ships by default).

Not shipped in v0.1: `update(agent_id, **kwargs)`. Agents are
effectively immutable — create a new one rather than mutating.

### SessionsClient

```python
class AsyncSessionsClient:
    async def create(
        self,
        *,
        agent_id: UUID,
        metadata: dict[str, object] | None = None,
    ) -> AsyncSessionHandle: ...

    async def get(self, session_id: UUID) -> AsyncSessionHandle: ...
    # raises NotFoundError or FailedPreconditionError if session is terminal
```

Sync mirror: `SyncSessionsClient`, returns `SessionHandle`.

Not shipped in v0.1: `list(agent_id=, status=, limit=)`. Iterate the
event log directly via `session.get_events(...)` or query Postgres
directly if you need cross-session reporting. A proper `list` surface
lands when there's a concrete use case.

### Session handles

`AsyncSessionHandle` / `SessionHandle` are the per-session objects
returned from `sessions.create` / `sessions.get`.

```python
class SessionHandle:
    id:        UUID
    agent_id:  UUID
    status:    SessionStatus      # ACTIVE | COMPLETED | FAILED | DELETED

    def send(self, content: str) -> Iterator[Event]:
        """Emit a user message, run the harness, stream events.

        Yields every new event the harness emits (assistant deltas,
        the is_complete=True closer, tool_call / tool_result events,
        system_event and harness_event records). Returns when the
        harness marks the session COMPLETED. Re-raises any exception
        the harness raised.
        """

    def get_events(
        self,
        *,
        start: int | None = None,   # inclusive lower seq (default 1)
        end: int | None = None,     # inclusive upper seq (default open)
        types: list[EventType] | None = None,
        limit: int = 1000,
    ) -> list[Event]: ...

    def complete(self) -> None:
        """Idempotent mark-terminal. No-op if already terminal."""
```

`AsyncSessionHandle` mirrors with `async def` and
`send() -> AsyncIterator[Event]`.

**`send()` polls every 50 ms** against `get_events(start=last_seq+1)`
until the harness task completes, then drains trailing events. Push
streaming via Postgres `LISTEN/NOTIFY` is deferred to v0.2; 50 ms
matches human perception for interactive use.

### VaultClient

```python
class AsyncVaultClient:
    def set(self, name: str, value: str) -> None: ...   # encrypts + stores atomically
    def get(self, name: str) -> str: ...                # raises VaultError
    def remove(self, name: str) -> bool: ...            # returns False if absent
    def list(self) -> list[str]: ...                    # names only (plaintext)
```

Vault operations are synchronous even on `AsyncTename` — the
underlying `Vault` class holds an `RLock` and writes atomically. The
sync client is the async client; no wrapping needed.

## Events

```python
from tename import Event, EventType

class Event:
    id:         UUID
    sequence:   int                # monotonic per-session, assigned by Session Service
    type:       EventType          # enum; see below
    payload:    dict[str, Any]
    created_at: datetime


class EventType(StrEnum):
    USER_MESSAGE      = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"   # payload has is_complete: bool + content
    TOOL_CALL         = "tool_call"           # payload: tool_id, tool_name, input
    TOOL_RESULT       = "tool_result"         # payload: tool_call_id, tool_name, is_error, content, ...
    SYSTEM_EVENT      = "system_event"        # payload.type: system_prompt | sandbox_provisioned | ...
    HARNESS_EVENT     = "harness_event"       # payload.type: compaction | plan | subagent_spawn
    ERROR             = "error"               # payload: message, retryable, status_code
```

Typical consumer:

```python
for event in session.send("..."):
    if event.type == EventType.ASSISTANT_MESSAGE:
        if event.payload.get("is_complete"):
            print(event.payload["content"])
    elif event.type == EventType.TOOL_CALL:
        print(f"[tool] {event.payload['tool_name']}({event.payload.get('input', {})})")
    elif event.type == EventType.TOOL_RESULT:
        if event.payload.get("is_error"):
            print(f"[tool error] {event.payload.get('error')}")
```

The event stream IS the API. We deliberately don't hide it behind
callbacks or specialized methods — once you know the event types,
every session is legible.

**Assistant streaming shape.** For each model turn the harness emits:

1. Zero or more `assistant_message(is_complete=False)` events — one
   per text delta.
2. Zero or more `tool_call` events.
3. Exactly one `assistant_message(is_complete=True)` with the full
   concatenated text (only when there was accumulated text).
4. Zero or more `tool_result` events (one per tool_call from step 2).

`build_context` skips `is_complete=False` events on replay, so the
closer is the canonical turn record.

## Typed exceptions

```python
from tename import (
    TenameError,         # base
    ConfigurationError,  # missing env var or kwarg
    NotFoundError,       # agent/session doesn't exist
    ValidationError,     # bad input per the message
    ModelError,          # provider returned an error; has .provider/.code/.retry_after
    SandboxError,        # sandbox provision/execute/destroy failed
    VaultError,          # wrong passphrase / corrupted file / missing credential
)
```

Each maps to a specific remediation — users can branch on the type
without parsing messages:

```python
try:
    for event in session.send("..."):
        ...
except ModelError as e:
    if e.code == "rate_limit":
        time.sleep(e.retry_after or 30)
        retry()
    else:
        raise
```

`ModelError` wraps the `error` chunk shape from the Model Router
(`message`, `provider`, `code`, `retry_after`). Other errors are
direct message-only exceptions.

## Configuration

Precedence (highest wins):

1. Explicit `Tename(...)` kwargs.
2. Environment variables: `TENAME_DATABASE_URL` (required),
   `ANTHROPIC_API_KEY`, `TENAME_VAULT_PASSPHRASE`,
   `TENAME_PROFILES_DIR`.
3. Hardcoded defaults: `vault_path = ~/.tename/vault.json.enc`,
   profiles from the bundled `tename.profiles` package.

Missing `TENAME_DATABASE_URL` (and no explicit `database_url`) →
`ConfigurationError` with a message naming the env var. Every other
value has a safe default or is optional.

**No config file in v0.1.** A TOML/YAML config file may arrive in
v0.2 if users find it easier than env vars; see
`tename.sdk._config.resolve_config` for the current resolution path.

## Not in v0.1 (explicit scope)

- **Remote / client-server mode.** Runs in-process only. The layering
  is structured so transport can swap later without a public API
  break.
- **Webhooks / event subscriptions.** Users poll via `get_events`
  or iterate `send()`.
- **Batch session operations.** One session at a time via the SDK.
- **Custom tool registration through the SDK.** Use the
  `@proxy_tool` decorator at module import time; the decorator
  registers globally and the harness picks it up automatically.
- **Agent mutation.** No `update()`. Create a new agent instead.
- **Session listing / search.** No `sessions.list()`. Query Postgres
  directly for cross-session analytics.
- **Config file.** Env vars + kwargs only.
- **Multi-user / auth.** Single-user, local-first.

Most of these come back as v0.2+ concrete proposals driven by
feedback. The SDK's layering is the contract — keeping the in-process
path clean is what makes a future server mode additive.
