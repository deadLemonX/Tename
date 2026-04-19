"""SDK-facing `Event` type.

The Session Service emits `tename.sessions.Event`. The SDK re-exports
that same Pydantic model rather than introducing a parallel type — so
`event.type`, `event.payload`, `event.sequence` etc. stay consistent
whether users peek at `get_events()` or iterate `session.send()`.
"""

from __future__ import annotations

from tename.sessions.models import Event, EventType

__all__ = ["Event", "EventType"]
