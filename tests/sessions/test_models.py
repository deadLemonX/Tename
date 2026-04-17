"""Unit tests for the Session Service Pydantic models.

Pure validation — no database, no async. Keeps a fast feedback loop
for anyone changing the public type surface.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError

from tename.sessions import Event, EventType, Session, SessionStatus


def _now() -> datetime:
    return datetime.now(tz=UTC)


def test_session_status_terminal_set() -> None:
    """is_terminal must reflect the architectural definition exactly."""
    session = _make_session(status=SessionStatus.ACTIVE)
    assert session.is_terminal is False

    for terminal in (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.DELETED):
        session = _make_session(status=terminal)
        assert session.is_terminal is True


def test_session_request_id_property() -> None:
    session = _make_session(metadata={"request_id": "req-42"})
    assert session.request_id == "req-42"

    session = _make_session(metadata={})
    assert session.request_id is None

    # Non-string values in metadata must not leak through the property.
    session = _make_session(metadata={"request_id": 42})
    assert session.request_id is None


def test_session_rejects_unknown_status() -> None:
    with pytest.raises(PydanticValidationError):
        Session(
            id=uuid4(),
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            agent_id=uuid4(),
            status="not-a-status",  # type: ignore[arg-type]
            last_sequence=0,
            metadata={},
            created_at=_now(),
            updated_at=_now(),
        )


def test_session_rejects_negative_sequence() -> None:
    with pytest.raises(PydanticValidationError):
        _make_session(last_sequence=-1)


def test_session_rejects_extra_fields() -> None:
    with pytest.raises(PydanticValidationError):
        Session(
            id=uuid4(),
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            agent_id=uuid4(),
            status=SessionStatus.ACTIVE,
            last_sequence=0,
            metadata={},
            created_at=_now(),
            updated_at=_now(),
            unexpected="boom",  # type: ignore[call-arg]
        )


def test_session_is_frozen() -> None:
    session = _make_session()
    with pytest.raises(PydanticValidationError):
        session.last_sequence = 99  # type: ignore[misc]


def test_event_type_enum_values() -> None:
    assert EventType.USER_MESSAGE.value == "user_message"
    assert EventType.ASSISTANT_MESSAGE.value == "assistant_message"
    assert EventType.TOOL_CALL.value == "tool_call"
    assert EventType.TOOL_RESULT.value == "tool_result"
    assert EventType.HARNESS_EVENT.value == "harness_event"
    assert EventType.SYSTEM_EVENT.value == "system_event"
    assert EventType.ERROR.value == "error"


def test_event_rejects_zero_sequence() -> None:
    """Sequences are 1-indexed per the data model."""
    with pytest.raises(PydanticValidationError):
        Event(
            id=uuid4(),
            session_id=uuid4(),
            sequence=0,
            type=EventType.USER_MESSAGE,
            payload={},
            created_at=_now(),
        )


def test_event_coerces_string_uuid() -> None:
    """Wire-format UUID strings are acceptable inputs."""
    raw_id = "11111111-1111-1111-1111-111111111111"
    event = Event(
        id=raw_id,  # type: ignore[arg-type]
        session_id=raw_id,  # type: ignore[arg-type]
        sequence=1,
        type=EventType.USER_MESSAGE,
        payload={"content": "hi"},
        created_at=_now(),
    )
    assert event.id == UUID(raw_id)


def _make_session(
    *,
    status: SessionStatus = SessionStatus.ACTIVE,
    metadata: dict[str, object] | None = None,
    last_sequence: int = 0,
) -> Session:
    return Session(
        id=uuid4(),
        tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
        agent_id=uuid4(),
        status=status,
        last_sequence=last_sequence,
        metadata=metadata or {},
        created_at=_now(),
        updated_at=_now(),
    )
