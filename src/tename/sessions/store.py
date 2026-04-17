"""Async SQLAlchemy Core queries for the Session Service.

The store is deliberately thin: it executes SQL, translates rows into
Pydantic models, and raises service-level exceptions. Business rules
(idempotency, terminal-state checks, structured logging) live in
`service.py`.

Keeping store queries flat — no ORM sessions, no unit-of-work — makes
the SQL easy to audit and keeps the service's advisory-lock dance in
S4 predictable.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from tename.sessions.exceptions import NotFoundError
from tename.sessions.models import Session, SessionStatus
from tename.sessions.schema import sessions as sessions_table


def _row_to_session(row: Any) -> Session:
    return Session(
        id=row.id,
        tenant_id=row.tenant_id,
        agent_id=row.agent_id,
        status=SessionStatus(row.status),
        last_sequence=row.last_sequence,
        metadata=dict(row.metadata) if row.metadata is not None else {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def insert_session(
    conn: AsyncConnection,
    *,
    agent_id: UUID,
    metadata: dict[str, Any],
) -> Session:
    """Insert a new session row and return it fully populated.

    Relies on server-side defaults for id, tenant_id, status,
    last_sequence, created_at, updated_at.
    """
    stmt = (
        sessions_table.insert()
        .values(agent_id=agent_id, metadata=metadata)
        .returning(*sessions_table.c)
    )
    result = await conn.execute(stmt)
    row = result.one()
    return _row_to_session(row)


async def find_session_by_request_id(
    conn: AsyncConnection,
    *,
    request_id: str,
    tenant_id: UUID,
) -> Session | None:
    """Look up a session by the `request_id` key stored in metadata.

    Uses the partial unique index `uq_sessions_request_id` for O(1)
    resolution. Returns `None` when no match is found.
    """
    stmt = select(*sessions_table.c).where(
        sessions_table.c.tenant_id == tenant_id,
        sessions_table.c.metadata["request_id"].astext == request_id,
    )
    result = await conn.execute(stmt)
    row = result.first()
    return _row_to_session(row) if row is not None else None


async def get_session(conn: AsyncConnection, session_id: UUID) -> Session:
    """Fetch a session by id. Raises NotFoundError if absent."""
    stmt = select(*sessions_table.c).where(sessions_table.c.id == session_id)
    result = await conn.execute(stmt)
    row = result.first()
    if row is None:
        raise NotFoundError(f"session {session_id} does not exist")
    return _row_to_session(row)


async def update_session_status(
    conn: AsyncConnection,
    session_id: UUID,
    status: SessionStatus,
) -> None:
    """Transition a session to a new status. Test-only for S3."""
    stmt = (
        sessions_table.update().where(sessions_table.c.id == session_id).values(status=status.value)
    )
    await conn.execute(stmt)


__all__ = [
    "find_session_by_request_id",
    "get_session",
    "insert_session",
    "update_session_status",
]
