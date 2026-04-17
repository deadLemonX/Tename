"""Integration tests for SessionService (create_session + wake).

These tests require a live Postgres. When `TENAME_TEST_DATABASE_URL` is
unset the fixtures skip the whole module.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tename.sessions import (
    FailedPreconditionError,
    NotFoundError,
    SessionService,
    SessionStatus,
    ValidationError,
)

pytestmark = pytest.mark.postgres


async def test_create_and_wake_roundtrip(service: SessionService, agent_id: UUID) -> None:
    created = await service.create_session(agent_id, metadata={"run": "smoke-1"})

    assert created.agent_id == agent_id
    assert created.status == SessionStatus.ACTIVE
    assert created.last_sequence == 0
    assert created.metadata == {"run": "smoke-1"}
    assert created.tenant_id == UUID("00000000-0000-0000-0000-000000000000")

    woken = await service.wake(created.id)
    assert woken == created


async def test_wake_not_found(service: SessionService, clean_db: None) -> None:
    with pytest.raises(NotFoundError):
        await service.wake(uuid4())


async def test_wake_terminal_state_rejected(
    service: SessionService, engine: AsyncEngine, agent_id: UUID
) -> None:
    session = await service.create_session(agent_id)

    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE sessions SET status = 'completed' WHERE id = :id"),
            {"id": str(session.id)},
        )

    with pytest.raises(FailedPreconditionError):
        await service.wake(session.id)


async def test_idempotency_same_request_id(service: SessionService, agent_id: UUID) -> None:
    first = await service.create_session(agent_id, request_id="req-abc")
    second = await service.create_session(agent_id, request_id="req-abc")

    assert first.id == second.id
    assert first.metadata["request_id"] == "req-abc"

    # A distinct request_id must produce a new session.
    third = await service.create_session(agent_id, request_id="req-xyz")
    assert third.id != first.id


async def test_idempotency_preserves_original_metadata(
    service: SessionService, agent_id: UUID
) -> None:
    first = await service.create_session(
        agent_id,
        metadata={"run": "first", "extra": 1},
        request_id="req-keep",
    )
    # Second call tries to change metadata; idempotency returns original.
    second = await service.create_session(
        agent_id,
        metadata={"run": "second"},
        request_id="req-keep",
    )

    assert first.id == second.id
    assert second.metadata == first.metadata
    assert second.metadata["run"] == "first"
    assert second.metadata["extra"] == 1


async def test_request_id_via_metadata_key(service: SessionService, agent_id: UUID) -> None:
    """Callers may supply request_id either as a kwarg or inside metadata."""
    first = await service.create_session(agent_id, metadata={"request_id": "req-via-meta"})
    second = await service.create_session(agent_id, request_id="req-via-meta")
    assert first.id == second.id


async def test_conflicting_request_id_raises(service: SessionService, agent_id: UUID) -> None:
    with pytest.raises(ValidationError):
        await service.create_session(
            agent_id,
            metadata={"request_id": "one"},
            request_id="two",
        )


async def test_create_with_empty_metadata(service: SessionService, agent_id: UUID) -> None:
    session = await service.create_session(agent_id)
    assert session.metadata == {}
    assert session.request_id is None
