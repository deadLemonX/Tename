# Python SDK Design

## Design goals

1. **Natural Python.** Feels like a normal Python library, not something translated from another language.
2. **Obvious first use.** `Tename()` without arguments should work if env vars are set.
3. **Streaming feels easy.** Iterating over events is the default pattern.
4. **Typed throughout.** IDE autocomplete just works. `pyright` is happy.
5. **Error messages teach.** Every error tells the user what to do next.

## The hello-world

This is the experience we optimize for:

```python
from tename_sdk import Tename  # placeholder name

client = Tename()  # reads ANTHROPIC_API_KEY and DATABASE_URL from env

agent = client.agents.create(
    name="assistant",
    model="claude-opus-4-6",
    system_prompt="You are a helpful assistant."
)

session = client.sessions.create(agent_id=agent.id)

for event in session.send("What's 2 + 2?"):
    if event.type == "assistant_message":
        print(event.payload["content"], end="", flush=True)
```

That's it. From zero to a working agent in 10 lines of Python.

## Top-level API surface

### Tename client

```python
class Tename:
    def __init__(
        self,
        database_url: str | None = None,  # defaults to env Tename_DATABASE_URL or sqlite
        anthropic_api_key: str | None = None,  # defaults to env ANTHROPIC_API_KEY
        openai_api_key: str | None = None,  # defaults to env OPENAI_API_KEY
        profiles_dir: str | None = None,  # defaults to built-in profiles + env override
        vault_path: str | None = None,  # defaults to ~/.tename/vault.json.enc
    ):
        ...
    
    @property
    def agents(self) -> AgentsClient:
        ...
    
    @property
    def sessions(self) -> SessionsClient:
        ...
    
    @property
    def profiles(self) -> ProfilesClient:
        ...
    
    @property
    def vault(self) -> VaultClient:
        ...
```

### AgentsClient

```python
class AgentsClient:
    def create(
        self,
        name: str,
        model: str,
        system_prompt: str | None = None,
        tools: list[str] | None = None,
        framework: str = "vanilla",
        sandbox_recipe: SandboxRecipe | None = None,
    ) -> Agent:
        ...
    
    def get(self, agent_id: str) -> Agent:
        ...
    
    def list(self) -> list[Agent]:
        ...
    
    def update(self, agent_id: str, **kwargs) -> Agent:
        ...
    
    def delete(self, agent_id: str) -> None:
        ...
```

### SessionsClient + Session

```python
class SessionsClient:
    def create(self, agent_id: str, metadata: dict | None = None) -> Session:
        ...
    
    def get(self, session_id: str) -> Session:
        ...
    
    def list(
        self, 
        agent_id: str | None = None, 
        status: str | None = None,
        limit: int = 100,
    ) -> list[Session]:
        ...

class Session:
    id: str
    agent_id: str
    status: str
    created_at: datetime
    
    def send(self, content: str) -> Iterator[Event]:
        """Send a message and stream events as they arrive."""
        ...
    
    def get_events(
        self,
        start: int | None = None,
        end: int | None = None,
        types: list[str] | None = None,
    ) -> list[Event]:
        """Retrieve events from the session log."""
        ...
    
    def complete(self) -> None:
        """Mark the session as completed."""
        ...
```

### Async variant

For long-running services, an async variant with the same surface:

```python
from tename_sdk import AsyncTename

async def main():
    client = AsyncTename()
    agent = await client.agents.create(...)
    session = await client.sessions.create(agent_id=agent.id)
    async for event in session.send("..."):
        ...
```

## Event types exposed via SDK

```python
class Event:
    id: str
    sequence: int
    type: str  # "user_message", "assistant_message", "tool_call", "tool_result", "harness_event", "system_event", "error"
    payload: dict
    created_at: datetime

# Users typically do:
for event in session.send(...):
    if event.type == "assistant_message":
        ...
    elif event.type == "tool_call":
        ...
```

We deliberately don't hide the event type system behind callbacks or specialized methods. The event stream IS the API. Users learn the event types once and then everything is consistent.

## Error handling

```python
class TenameError(Exception):
    """Base class for all SDK errors."""

class ConfigurationError(TenameError):
    """Missing or invalid configuration. Actionable: fix env vars or config."""

class NotFoundError(TenameError):
    """Resource doesn't exist. Actionable: check ID."""

class ValidationError(TenameError):
    """Bad input. Actionable: fix the input per the error message."""

class ModelError(TenameError):
    """Model provider returned an error. Actionable: depends on code (rate limit, bad API key, etc.)"""
    provider: str
    code: str
    retry_after: int | None

class SandboxError(TenameError):
    """Sandbox operation failed. Actionable: check Docker is running, container logs."""

class VaultError(TenameError):
    """Vault operation failed. Actionable: check passphrase, vault file permissions."""
```

Every error type is specifically catchable. Users can write:

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

## Configuration sources

In priority order (highest wins):

1. Arguments to `Tename(...)`
2. Environment variables (`Tename_DATABASE_URL`, `ANTHROPIC_API_KEY`, etc.)
3. Config file at `~/.tename/config.yaml` (if present)
4. Hardcoded defaults (where sensible)

Missing required config → `ConfigurationError` with a clear message about what's missing and how to provide it.

## Not in v0.1

- Webhooks or event subscriptions for external notifications
- Batch session operations
- Custom tool registration via SDK (must be done via decorators in code)
- Multi-user / auth support
- Remote mode (connecting to a running Tename server)

These are v0.2+ if user demand exists.
