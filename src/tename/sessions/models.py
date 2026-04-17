"""Pydantic domain models for the Session Service.

These types are the public contract between the service, the harness,
and the SDK. They intentionally mirror `docs/architecture/data-model.md`
and do not depend on SQLAlchemy — callers should never have to import
database types to use the service.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SessionStatus(StrEnum):
    """Lifecycle states a session can be in. Terminal states are
    `completed`, `failed`, and `deleted` — waking one is an error."""

    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    DELETED = "deleted"


TERMINAL_STATUSES: frozenset[SessionStatus] = frozenset(
    {SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.DELETED}
)


class EventType(StrEnum):
    """Event kinds per `docs/architecture/data-model.md`. Payload
    schemas differ by type; validation lives in adapter-specific code,
    not here."""

    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    HARNESS_EVENT = "harness_event"
    SYSTEM_EVENT = "system_event"
    ERROR = "error"


class Agent(BaseModel):
    """Reusable agent configuration. Populated by the SDK in S10; the
    Session Service only reads the row to discover which profile to
    load during `wake`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    tenant_id: UUID
    name: str
    model: str
    framework: str = "vanilla"
    system_prompt: str | None = None
    tools: list[str] = Field(default_factory=list)
    sandbox_recipe: dict[str, Any] | None = None
    created_at: datetime


class Session(BaseModel):
    """A single durable run of an agent. Events append under its id.

    `metadata` is arbitrary user context; the service reserves the key
    `request_id` for idempotency and will reject a conflicting explicit
    argument.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    status: SessionStatus
    last_sequence: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def request_id(self) -> str | None:
        value = self.metadata.get("request_id")
        return value if isinstance(value, str) else None


class Event(BaseModel):
    """Append-only record of a single happening within a session.

    `id` is client-supplied for idempotency — duplicate writes with the
    same `(session_id, id)` return the original row untouched.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    session_id: UUID
    sequence: int = Field(ge=1)
    type: EventType
    payload: dict[str, Any]
    created_at: datetime


__all__ = [
    "TERMINAL_STATUSES",
    "Agent",
    "Event",
    "EventType",
    "Session",
    "SessionStatus",
]
