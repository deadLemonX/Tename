"""Session Service: append-only, idempotent event log for agent runs.

Public surface: `SessionService` plus the Pydantic models and typed
exceptions. See `docs/architecture/session-service.md`.
"""

from tename.sessions.exceptions import (
    FailedPreconditionError,
    NotFoundError,
    SessionServiceError,
    ValidationError,
)
from tename.sessions.models import (
    Agent,
    Event,
    EventType,
    Session,
    SessionStatus,
)
from tename.sessions.service import (
    MAX_EVENTS_LIMIT,
    MAX_PAYLOAD_BYTES,
    SessionService,
)

__all__ = [
    "MAX_EVENTS_LIMIT",
    "MAX_PAYLOAD_BYTES",
    "Agent",
    "Event",
    "EventType",
    "FailedPreconditionError",
    "NotFoundError",
    "Session",
    "SessionService",
    "SessionServiceError",
    "SessionStatus",
    "ValidationError",
]
