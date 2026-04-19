"""Tename: open-source, model-agnostic runtime for AI agents.

The primary entry point is `Tename` (synchronous) or `AsyncTename`.
Both live in `tename.sdk` and are re-exported here for convenience::

    from tename import Tename

    with Tename() as client:
        agent = client.agents.create(...)
"""

from tename.sdk import (
    AsyncSessionHandle,
    AsyncTename,
    ConfigurationError,
    Event,
    EventType,
    ModelError,
    NotFoundError,
    SandboxError,
    SessionHandle,
    Tename,
    TenameError,
    ValidationError,
    VaultError,
)

__version__ = "0.1.0"

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
    "__version__",
]
