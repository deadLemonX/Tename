"""Exceptions raised by the Session Service.

Callers import these to distinguish expected failure modes (missing
session, terminal state, invalid input) from unexpected ones. Keeping
them in their own module avoids import cycles with the models.
"""

from __future__ import annotations


class SessionServiceError(Exception):
    """Base for all Session Service exceptions."""


class NotFoundError(SessionServiceError):
    """Raised when a session or agent does not exist."""


class FailedPreconditionError(SessionServiceError):
    """Raised when the target session is in a state incompatible with
    the requested operation (e.g. waking a completed session)."""


class ValidationError(SessionServiceError):
    """Raised when a caller-supplied argument is structurally invalid
    (e.g. a metadata dict that conflicts with an explicit request_id).

    This is distinct from Pydantic's own validation failure, which
    fires earlier at the model construction boundary.
    """


__all__ = [
    "FailedPreconditionError",
    "NotFoundError",
    "SessionServiceError",
    "ValidationError",
]
