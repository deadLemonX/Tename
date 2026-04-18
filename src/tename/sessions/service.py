"""Public API for the Session Service.

Scope: `create_session` (idempotent on request_id), `wake`,
`emit_event` (advisory-lock serialized, idempotent on event_id), and
`get_events` (keyset pagination over sequence).

The service owns its own async engine. Callers pass a SQLAlchemy URL
on construction and `await service.close()` when finished. Integration
tests and the SDK share this entry point.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tename.sessions.exceptions import FailedPreconditionError, ValidationError
from tename.sessions.models import TERMINAL_STATUSES, Event, EventType, Session
from tename.sessions.schema import DEFAULT_TENANT_ID
from tename.sessions.store import (
    acquire_session_advisory_lock,
    find_event_by_id,
    find_session_by_request_id,
    get_session,
    insert_event_and_bump_sequence,
    insert_session,
    load_session_for_emit,
    select_events,
)

logger = logging.getLogger(__name__)

_DEFAULT_TENANT_UUID = UUID(DEFAULT_TENANT_ID)

MAX_PAYLOAD_BYTES = 256 * 1024
"""Hard cap on serialized payload size (256 KiB).

Future versions may offload oversized payloads to object storage; v0.1
rejects them outright so the append-only invariant on the events table
is never violated by a too-big write mid-session.
"""

DEFAULT_EVENTS_LIMIT = 1000
MAX_EVENTS_LIMIT = 10000


def _encode_payload(payload: dict[str, Any]) -> bytes:
    """Serialize a payload to the bytes that will hit JSONB.

    We use UTF-8 JSON as the measurement basis. This is slightly
    conservative vs. Postgres' internal JSONB representation, which is
    fine for a 256 KiB cap — the goal is to reject pathological inputs
    before they touch the network.
    """
    try:
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"event payload is not JSON-serializable: {exc}") from exc


class SessionService:
    """Durable, append-only session store.

    One instance owns one SQLAlchemy async engine and therefore one
    connection pool. Safe to share across concurrent tasks.
    """

    def __init__(self, database_url: str, *, echo: bool = False) -> None:
        if not database_url:
            raise ValueError("database_url must be a non-empty SQLAlchemy URL")
        self._engine: AsyncEngine = create_async_engine(
            database_url,
            echo=echo,
            future=True,
        )

    async def close(self) -> None:
        """Dispose the engine's connection pool. Idempotent."""
        await self._engine.dispose()

    async def create_session(
        self,
        agent_id: UUID,
        *,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> Session:
        """Create an active session for `agent_id`.

        When `request_id` is provided, the call is idempotent: repeat
        invocations with the same `request_id` return the original
        session without creating a duplicate. The value is persisted
        inside the session's metadata JSONB.

        Raises:
            ValidationError: `metadata` contains a `request_id` that
                conflicts with the explicit argument.
        """
        merged_metadata = self._merge_metadata(metadata, request_id)
        resolved_request_id = merged_metadata.get("request_id")
        log_ctx: dict[str, Any] = {
            "agent_id": str(agent_id),
            "request_id": resolved_request_id,
        }

        async with self._engine.begin() as conn:
            if isinstance(resolved_request_id, str):
                existing = await find_session_by_request_id(
                    conn,
                    request_id=resolved_request_id,
                    tenant_id=_DEFAULT_TENANT_UUID,
                )
                if existing is not None:
                    logger.info(
                        "session.create.idempotent_hit",
                        extra={**log_ctx, "session_id": str(existing.id)},
                    )
                    return existing

            try:
                session = await insert_session(
                    conn,
                    agent_id=agent_id,
                    metadata=merged_metadata,
                )
            except IntegrityError as exc:
                # Another writer won the race on the same request_id.
                # Roll back and re-read.
                if isinstance(resolved_request_id, str):
                    logger.info(
                        "session.create.idempotent_race",
                        extra=log_ctx,
                    )
                    await conn.rollback()
                    async with self._engine.begin() as retry_conn:
                        existing = await find_session_by_request_id(
                            retry_conn,
                            request_id=resolved_request_id,
                            tenant_id=_DEFAULT_TENANT_UUID,
                        )
                    if existing is not None:
                        return existing
                raise RuntimeError(
                    "session insert failed and idempotent retry found no row"
                ) from exc

        logger.info(
            "session.create.ok",
            extra={**log_ctx, "session_id": str(session.id)},
        )
        return session

    async def wake(self, session_id: UUID) -> Session:
        """Read-only lookup of a session.

        Raises:
            NotFoundError: session does not exist.
            FailedPreconditionError: session is in a terminal state
                (completed, failed, or deleted).
        """
        async with self._engine.connect() as conn:
            session = await get_session(conn, session_id)

        if session.is_terminal:
            logger.info(
                "session.wake.terminal",
                extra={
                    "session_id": str(session_id),
                    "status": session.status.value,
                },
            )
            raise FailedPreconditionError(
                f"session {session_id} is in terminal state {session.status.value}"
            )

        logger.info(
            "session.wake.ok",
            extra={"session_id": str(session_id)},
        )
        return session

    async def emit_event(
        self,
        session_id: UUID,
        *,
        event_id: UUID,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> Event:
        """Append an event to a session's log, idempotent on event_id.

        Behavior (principles #4 and #6):
        1. Serialize payload and reject if > 256 KiB.
        2. Inside a single transaction, take a session-scoped advisory
           lock so concurrent emitters for the same session are
           serialized.
        3. If an event with (session_id, event_id) already exists,
           return it unchanged — safe replay.
        4. Load the session's last_sequence and status. Reject if the
           session is missing or in a terminal state.
        5. Insert the event at sequence = last_sequence + 1 and bump
           the session pointer. The UNIQUE (session_id, sequence)
           constraint is a final safety net.

        Raises:
            ValidationError: payload exceeds the size cap, or is not
                JSON-serializable.
            NotFoundError: session does not exist.
            FailedPreconditionError: session is in a terminal state.
        """
        payload_bytes = _encode_payload(payload)
        if len(payload_bytes) > MAX_PAYLOAD_BYTES:
            raise ValidationError(
                f"event payload is {len(payload_bytes)} bytes; max is {MAX_PAYLOAD_BYTES}"
            )

        log_ctx: dict[str, Any] = {
            "session_id": str(session_id),
            "event_id": str(event_id),
            "event_type": event_type.value,
        }

        async with self._engine.begin() as conn:
            await acquire_session_advisory_lock(conn, session_id)

            existing = await find_event_by_id(conn, session_id, event_id)
            if existing is not None:
                logger.info(
                    "session.emit.idempotent_hit",
                    extra={**log_ctx, "sequence": existing.sequence},
                )
                return existing

            last_sequence, status = await load_session_for_emit(conn, session_id)
            if status in TERMINAL_STATUSES:
                logger.info(
                    "session.emit.terminal",
                    extra={**log_ctx, "status": status.value},
                )
                raise FailedPreconditionError(
                    f"session {session_id} is in terminal state {status.value}"
                )

            new_sequence = last_sequence + 1
            try:
                event = await insert_event_and_bump_sequence(
                    conn,
                    session_id=session_id,
                    event_id=event_id,
                    event_type=event_type,
                    payload=payload,
                    new_sequence=new_sequence,
                )
            except IntegrityError as exc:
                # Should not happen while holding the advisory lock, but
                # surface a clean error rather than a raw DB exception.
                raise RuntimeError(
                    f"event insert for session {session_id} violated a unique constraint "
                    "despite holding the advisory lock"
                ) from exc

        logger.info(
            "session.emit.ok",
            extra={**log_ctx, "sequence": event.sequence},
        )
        return event

    async def get_events(
        self,
        session_id: UUID,
        *,
        start: int | None = None,
        end: int | None = None,
        types: list[EventType] | None = None,
        limit: int = DEFAULT_EVENTS_LIMIT,
    ) -> list[Event]:
        """Read events from a session in ascending sequence order.

        Parameters:
            start: inclusive lower bound on sequence (default 1).
            end: inclusive upper bound on sequence (default unbounded).
            types: optional filter to one or more event types.
            limit: maximum events returned, capped at 10_000.

        The performance-critical path is "read the tail of a live
        session" — served by `idx_events_session_seq`. Does NOT check
        whether the session exists; an empty list is a valid answer
        for an unknown id.
        """
        if limit < 1:
            raise ValidationError(f"limit must be >= 1 (got {limit})")
        effective_limit = min(limit, MAX_EVENTS_LIMIT)
        effective_start = 1 if start is None else start
        if effective_start < 1:
            raise ValidationError(f"start must be >= 1 (got {start})")
        if end is not None and end < effective_start:
            raise ValidationError(f"end ({end}) must be >= start ({effective_start})")

        async with self._engine.connect() as conn:
            events = await select_events(
                conn,
                session_id=session_id,
                start=effective_start,
                end=end,
                types=types,
                limit=effective_limit,
            )

        logger.info(
            "session.get_events.ok",
            extra={
                "session_id": str(session_id),
                "returned": len(events),
                "start": effective_start,
                "end": end,
                "limit": effective_limit,
            },
        )
        return events

    @staticmethod
    def _merge_metadata(
        metadata: dict[str, Any] | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        """Combine caller metadata with an explicit request_id.

        Rules:
        - If both are given and disagree, raise ValidationError.
        - If only `request_id` is given, inject it.
        - If only `metadata['request_id']` is given, keep it.
        - Neither is given → return a copy (empty-dict safe).
        """
        merged: dict[str, Any] = dict(metadata) if metadata else {}
        metadata_request_id = merged.get("request_id")

        if (
            request_id is not None
            and metadata_request_id is not None
            and metadata_request_id != request_id
        ):
            raise ValidationError(
                "request_id argument conflicts with metadata['request_id']: "
                f"{request_id!r} != {metadata_request_id!r}"
            )

        if request_id is not None:
            merged["request_id"] = request_id
        return merged


__all__ = ["SessionService"]
