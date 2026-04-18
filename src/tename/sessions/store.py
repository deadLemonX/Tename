"""Async SQLAlchemy Core queries for the Session Service.

The store is deliberately thin: it executes SQL, translates rows into
Pydantic models, and raises service-level exceptions. Business rules
(idempotency, terminal-state checks, structured logging) live in
`service.py`.

Keeping store queries flat — no ORM sessions, no unit-of-work — makes
the SQL easy to audit and keeps the service's advisory-lock dance
predictable.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from tename.sessions.exceptions import NotFoundError
from tename.sessions.models import Agent, Event, EventType, Session, SessionStatus
from tename.sessions.schema import agents as agents_table
from tename.sessions.schema import events as events_table
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


def _row_to_agent(row: Any) -> Agent:
    return Agent(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        model=row.model,
        framework=row.framework,
        system_prompt=row.system_prompt,
        tools=list(row.tools) if row.tools is not None else [],
        sandbox_recipe=dict(row.sandbox_recipe) if row.sandbox_recipe is not None else None,
        created_at=row.created_at,
    )


def _row_to_event(row: Any) -> Event:
    return Event(
        id=row.id,
        session_id=row.session_id,
        sequence=row.sequence,
        type=EventType(row.type),
        payload=dict(row.payload) if row.payload is not None else {},
        created_at=row.created_at,
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


async def get_agent(conn: AsyncConnection, agent_id: UUID) -> Agent:
    """Fetch an agent by id. Raises NotFoundError if absent."""
    stmt = select(*agents_table.c).where(agents_table.c.id == agent_id)
    result = await conn.execute(stmt)
    row = result.first()
    if row is None:
        raise NotFoundError(f"agent {agent_id} does not exist")
    return _row_to_agent(row)


async def mark_session_complete(conn: AsyncConnection, session_id: UUID) -> SessionStatus:
    """Transition an active session to COMPLETED; no-op on terminal state.

    Returns the resulting status. Raises NotFoundError if absent.
    """
    session = await get_session(conn, session_id)
    if session.is_terminal:
        return session.status
    stmt = (
        sessions_table.update()
        .where(sessions_table.c.id == session_id)
        .values(status=SessionStatus.COMPLETED.value, updated_at=text("NOW()"))
    )
    await conn.execute(stmt)
    return SessionStatus.COMPLETED


async def acquire_session_advisory_lock(conn: AsyncConnection, session_id: UUID) -> None:
    """Take a transaction-scoped advisory lock keyed on session_id.

    Serializes concurrent `emit_event` calls for the same session so
    sequence numbers advance without gaps or duplicates. The lock is
    released automatically on COMMIT/ROLLBACK.
    """
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:sid, 0))"),
        {"sid": str(session_id)},
    )


async def find_event_by_id(conn: AsyncConnection, session_id: UUID, event_id: UUID) -> Event | None:
    """Look up an event by its (session_id, id) primary key."""
    stmt = select(*events_table.c).where(
        events_table.c.session_id == session_id,
        events_table.c.id == event_id,
    )
    result = await conn.execute(stmt)
    row = result.first()
    return _row_to_event(row) if row is not None else None


async def load_session_for_emit(
    conn: AsyncConnection, session_id: UUID
) -> tuple[int, SessionStatus]:
    """Read last_sequence and status under the current transaction.

    Caller must already hold the advisory lock. Returns the pair so
    emit_event can make both its terminal-state and sequence decisions
    without a second round-trip.
    """
    stmt = select(sessions_table.c.last_sequence, sessions_table.c.status).where(
        sessions_table.c.id == session_id
    )
    result = await conn.execute(stmt)
    row = result.first()
    if row is None:
        raise NotFoundError(f"session {session_id} does not exist")
    return int(row.last_sequence), SessionStatus(row.status)


async def insert_event_and_bump_sequence(
    conn: AsyncConnection,
    *,
    session_id: UUID,
    event_id: UUID,
    event_type: EventType,
    payload: dict[str, Any],
    new_sequence: int,
) -> Event:
    """Insert an event at `new_sequence` and update sessions.last_sequence.

    Must run inside the advisory-locked transaction from
    `acquire_session_advisory_lock`. The UNIQUE (session_id, sequence)
    constraint is the last line of defense if a caller skips the lock.
    """
    insert_stmt = (
        events_table.insert()
        .values(
            id=event_id,
            session_id=session_id,
            sequence=new_sequence,
            type=event_type.value,
            payload=payload,
        )
        .returning(*events_table.c)
    )
    result = await conn.execute(insert_stmt)
    event = _row_to_event(result.one())

    update_stmt = (
        sessions_table.update()
        .where(sessions_table.c.id == session_id)
        .values(last_sequence=new_sequence, updated_at=text("NOW()"))
    )
    await conn.execute(update_stmt)
    return event


async def select_events(
    conn: AsyncConnection,
    *,
    session_id: UUID,
    start: int,
    end: int | None,
    types: list[EventType] | None,
    limit: int,
) -> list[Event]:
    """Keyset-paginated read over events.

    Sequence is monotonic and densely packed within a session, so a
    BETWEEN range on `sequence` is the natural keyset cursor. Both
    bounds are inclusive.
    """
    conditions = [
        events_table.c.session_id == session_id,
        events_table.c.sequence >= start,
    ]
    if end is not None:
        conditions.append(events_table.c.sequence <= end)
    if types:
        conditions.append(events_table.c.type.in_([t.value for t in types]))

    stmt = (
        select(*events_table.c)
        .where(*conditions)
        .order_by(events_table.c.sequence.asc())
        .limit(limit)
    )
    result = await conn.execute(stmt)
    return [_row_to_event(row) for row in result]


__all__ = [
    "acquire_session_advisory_lock",
    "find_event_by_id",
    "find_session_by_request_id",
    "get_agent",
    "get_session",
    "insert_event_and_bump_sequence",
    "insert_session",
    "load_session_for_emit",
    "mark_session_complete",
    "select_events",
    "update_session_status",
]
