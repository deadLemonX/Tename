"""Session Service: append-only, idempotent event log for agent runs.

S3 ships the schema plus `create_session` and `wake`. S4 completes the
API with `emit_event` and `get_events`. See
`docs/architecture/session-service.md`.
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
from tename.sessions.service import SessionService

__all__ = [
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
