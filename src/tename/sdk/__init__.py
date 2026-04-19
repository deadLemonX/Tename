"""Python SDK: the developer-facing API surface for Tename.

Usage::

    from tename.sdk import Tename

    client = Tename()  # reads TENAME_DATABASE_URL + ANTHROPIC_API_KEY from env

    agent = client.agents.create(
        name="assistant",
        model="claude-opus-4-6",
        system_prompt="You are a helpful assistant.",
    )
    session = client.sessions.create(agent_id=agent.id)

    for event in session.send("What is 2+2?"):
        if event.type == "assistant_message":
            print(event.payload["content"], end="", flush=True)

    client.close()

The SDK exposes both a synchronous (`Tename`) and asynchronous
(`AsyncTename`) surface; both share the same internal services.
"""

from tename.sdk.client import AsyncTename, Tename
from tename.sdk.errors import (
    ConfigurationError,
    ModelError,
    NotFoundError,
    SandboxError,
    TenameError,
    ValidationError,
    VaultError,
)
from tename.sdk.events import Event, EventType
from tename.sdk.sessions import AsyncSessionHandle, SessionHandle

__all__ = [
    "AsyncSessionHandle",
    "AsyncTename",
    "ConfigurationError",
    "Event",
    "EventType",
    "ModelError",
    "NotFoundError",
    "SandboxError",
    "SessionHandle",
    "Tename",
    "TenameError",
    "ValidationError",
    "VaultError",
]
