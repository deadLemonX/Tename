"""Framework adapter interface and registry.

Every adapter owns the translation between a framework's message/tool
shapes and Tename's event log. The harness loop (S7) picks an adapter by
name from `agent.framework` and calls only through this interface.

Adapters are stateless. `get_adapter(name)` returns a fresh instance so
callers never share state by accident.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tename.harness.profiles import Profile
from tename.router.types import Message, ModelChunk, ToolDef
from tename.sessions.models import Agent, Event, EventType


class UnknownAdapterError(KeyError):
    """Raised when `get_adapter` is called with an unregistered name."""


class PendingEvent(BaseModel):
    """An event ready to be emitted but not yet persisted.

    The Session Service owns `sequence` and `created_at`; adapters can only
    produce `id` / `type` / `payload`. The S7 loop turns each `PendingEvent`
    into an `Event` via `SessionService.emit_event`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)


class FrameworkAdapter(ABC):
    """Abstract base class for framework adapters.

    Subclasses set a unique `name` class variable and implement the four
    abstract methods below. Register via `register_adapter(cls)` at import
    time so `get_adapter(name)` can find them.
    """

    name: ClassVar[str]

    @abstractmethod
    def build_context(self, events: Sequence[Event], profile: Profile) -> list[Message]:
        """Translate the event log into messages for the Model Router.

        Adapters must be pure: same events + profile in → same messages
        out. They receive no agent handle (the harness is stateless); the
        system prompt arrives as an event in the log.
        """

    @abstractmethod
    def chunk_to_event(self, chunk: ModelChunk) -> PendingEvent | None:
        """Translate a single streaming `ModelChunk` into a pending event.

        Some chunks (e.g. `done`, intermediate `tool_call_start` /
        `tool_call_delta`) produce no event on their own — return `None`
        in that case. The loop in S7 is responsible for emitting the
        returned `PendingEvent` to the Session Service, which assigns
        `sequence` and `created_at`.
        """

    @abstractmethod
    def get_tools(self, agent: Agent) -> list[ToolDef]:
        """Return tool definitions in this framework's expected shape."""

    def supports_streaming(self) -> bool:
        """Whether this adapter handles incremental chunks.

        Defaults to True because Tename's streaming-by-default design
        (principle #7) assumes adapters can surface partial output.
        Subclasses override only if the underlying framework truly cannot
        consume a stream.
        """
        return True


_ADAPTERS: dict[str, type[FrameworkAdapter]] = {}


def register_adapter(adapter_cls: type[FrameworkAdapter]) -> type[FrameworkAdapter]:
    """Register an adapter class under its `name`. Idempotent on identity.

    Usable as a class decorator. Re-registering the same class is a no-op;
    re-registering a different class under the same name raises.
    """
    name = adapter_cls.name
    existing = _ADAPTERS.get(name)
    if existing is adapter_cls:
        return adapter_cls
    if existing is not None:
        raise ValueError(
            f"adapter name '{name}' already registered to {existing.__name__}; "
            f"cannot re-register {adapter_cls.__name__}"
        )
    _ADAPTERS[name] = adapter_cls
    return adapter_cls


def get_adapter(name: str) -> FrameworkAdapter:
    """Instantiate a registered adapter by name."""
    try:
        cls = _ADAPTERS[name]
    except KeyError as exc:
        known = sorted(_ADAPTERS)
        raise UnknownAdapterError(f"no adapter registered for '{name}'. Known: {known}") from exc
    return cls()


def known_adapters() -> list[str]:
    """Names of registered adapters, sorted."""
    return sorted(_ADAPTERS)


__all__ = [
    "FrameworkAdapter",
    "PendingEvent",
    "UnknownAdapterError",
    "get_adapter",
    "known_adapters",
    "register_adapter",
]
