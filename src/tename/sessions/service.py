"""Public API for the Session Service.

v0.1 / S3 scope: `create_session` (idempotent on request_id) and
`wake`. `emit_event` and `get_events` land in S4.

The service owns its own async engine. Callers pass a SQLAlchemy URL
on construction and `await service.close()` when finished. Integration
tests and the SDK share this entry point.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tename.sessions.exceptions import FailedPreconditionError, ValidationError
from tename.sessions.models import Session
from tename.sessions.schema import DEFAULT_TENANT_ID
from tename.sessions.store import (
    find_session_by_request_id,
    get_session,
    insert_session,
)

logger = logging.getLogger(__name__)

_DEFAULT_TENANT_UUID = UUID(DEFAULT_TENANT_ID)


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
