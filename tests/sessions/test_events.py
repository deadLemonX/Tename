"""Integration tests for SessionService.emit_event + get_events.

These exercise the full S4 contract: idempotency, advisory-lock
concurrency, terminal-state rejection, pagination, filtering,
performance, and crash recovery across engine instances. All require a
live Postgres via TENAME_TEST_DATABASE_URL; the fixtures skip when it
is unset.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tename.sessions import (
    Event,
    EventType,
    FailedPreconditionError,
    NotFoundError,
    SessionService,
    ValidationError,
)

pytestmark = pytest.mark.postgres


# ---------- helpers ----------


async def _emit_user(
    service: SessionService,
    session_id: UUID,
    content: str,
    *,
    event_id: UUID | None = None,
) -> Event:
    return await service.emit_event(
        session_id,
        event_id=event_id or uuid4(),
        event_type=EventType.USER_MESSAGE,
        payload={"content": content},
    )


# ---------- tests ----------


async def test_session_lifecycle(service: SessionService, agent_id: UUID) -> None:
    """create → emit N events → read → sequences contiguous from 1."""
    session = await service.create_session(agent_id)

    emitted: list[Event] = []
    for i in range(10):
        emitted.append(await _emit_user(service, session.id, f"msg-{i}"))

    assert [e.sequence for e in emitted] == list(range(1, 11))

    events = await service.get_events(session.id)
    assert [e.sequence for e in events] == list(range(1, 11))
    assert [e.payload["content"] for e in events] == [f"msg-{i}" for i in range(10)]

    # Session pointer advanced.
    reloaded = await service.wake(session.id)
    assert reloaded.last_sequence == 10


async def test_emit_idempotency(service: SessionService, agent_id: UUID) -> None:
    """Same event_id submitted 100 times → one row, stable sequence."""
    session = await service.create_session(agent_id)
    event_id = uuid4()

    results: list[Event] = []
    for _ in range(100):
        results.append(
            await service.emit_event(
                session.id,
                event_id=event_id,
                event_type=EventType.USER_MESSAGE,
                payload={"content": "dedupe me"},
            )
        )

    # All 100 calls return the same event.
    assert len({e.id for e in results}) == 1
    assert len({e.sequence for e in results}) == 1
    assert results[0].sequence == 1

    # Exactly one row landed in the table.
    events = await service.get_events(session.id)
    assert len(events) == 1
    assert events[0].id == event_id

    # Session pointer did NOT advance beyond the single write.
    reloaded = await service.wake(session.id)
    assert reloaded.last_sequence == 1


async def test_concurrent_writers(service: SessionService, agent_id: UUID) -> None:
    """10 workers, 100 events each, 1000 unique contiguous sequences.

    Validates principle #6 (append-only) under concurrent emit: no
    gaps, no duplicates, advisory lock serializes correctly.
    """
    session = await service.create_session(agent_id)

    workers = 10
    per_worker = 100

    async def worker(worker_idx: int) -> list[int]:
        seqs: list[int] = []
        for j in range(per_worker):
            event = await service.emit_event(
                session.id,
                event_id=uuid4(),
                event_type=EventType.USER_MESSAGE,
                payload={"worker": worker_idx, "i": j},
            )
            seqs.append(event.sequence)
        return seqs

    results = await asyncio.gather(*(worker(i) for i in range(workers)))

    all_seqs = [s for seqs in results for s in seqs]
    assert len(all_seqs) == workers * per_worker
    assert len(set(all_seqs)) == workers * per_worker  # no duplicates
    assert sorted(all_seqs) == list(range(1, workers * per_worker + 1))  # no gaps

    reloaded = await service.wake(session.id)
    assert reloaded.last_sequence == workers * per_worker


async def test_large_payload_rejected(service: SessionService, agent_id: UUID) -> None:
    """>256 KiB payload rejected before a DB write is attempted."""
    session = await service.create_session(agent_id)

    huge = "x" * (300 * 1024)  # 300 KiB single string — safely over cap
    with pytest.raises(ValidationError) as exc_info:
        await service.emit_event(
            session.id,
            event_id=uuid4(),
            event_type=EventType.USER_MESSAGE,
            payload={"content": huge},
        )
    assert "256" in str(exc_info.value) or "bytes" in str(exc_info.value)

    # Nothing persisted — session pointer still at zero.
    reloaded = await service.wake(session.id)
    assert reloaded.last_sequence == 0


async def test_terminal_state_rejected(
    service: SessionService, engine: AsyncEngine, agent_id: UUID
) -> None:
    """emit on completed/failed/deleted session raises FailedPrecondition."""
    session = await service.create_session(agent_id)
    await _emit_user(service, session.id, "before close")

    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE sessions SET status = 'completed' WHERE id = :id"),
            {"id": str(session.id)},
        )

    with pytest.raises(FailedPreconditionError):
        await _emit_user(service, session.id, "after close")


async def test_emit_session_not_found(service: SessionService) -> None:
    """emit on a nonexistent session raises NotFoundError."""
    with pytest.raises(NotFoundError):
        await service.emit_event(
            uuid4(),
            event_id=uuid4(),
            event_type=EventType.USER_MESSAGE,
            payload={"content": "ghost"},
        )


async def test_get_events_pagination(service: SessionService, agent_id: UUID) -> None:
    """500 events, fetch in 5 batches of 100 — no overlap, no gap."""
    session = await service.create_session(agent_id)

    for i in range(500):
        await _emit_user(service, session.id, f"m-{i}")

    batches: list[list[Event]] = []
    cursor = 1
    while cursor <= 500:
        batch = await service.get_events(session.id, start=cursor, end=cursor + 99, limit=100)
        assert len(batch) == 100
        batches.append(batch)
        cursor += 100

    all_events = [e for batch in batches for e in batch]
    assert len(all_events) == 500
    assert [e.sequence for e in all_events] == list(range(1, 501))

    # `limit` alone still works (no bounds): get first 50.
    first_50 = await service.get_events(session.id, limit=50)
    assert [e.sequence for e in first_50] == list(range(1, 51))


async def test_get_events_filtering(service: SessionService, agent_id: UUID) -> None:
    """Emit mixed types, filter by type, verify only matches returned."""
    session = await service.create_session(agent_id)

    # Interleave USER_MESSAGE and ASSISTANT_MESSAGE.
    for i in range(10):
        await service.emit_event(
            session.id,
            event_id=uuid4(),
            event_type=EventType.USER_MESSAGE,
            payload={"i": i},
        )
        await service.emit_event(
            session.id,
            event_id=uuid4(),
            event_type=EventType.ASSISTANT_MESSAGE,
            payload={"i": i, "is_complete": True},
        )

    # Single-type filter.
    user_events = await service.get_events(session.id, types=[EventType.USER_MESSAGE])
    assert len(user_events) == 10
    assert {e.type for e in user_events} == {EventType.USER_MESSAGE}

    # Multi-type filter.
    both = await service.get_events(
        session.id, types=[EventType.USER_MESSAGE, EventType.ASSISTANT_MESSAGE]
    )
    assert len(both) == 20

    # Non-matching filter returns empty.
    tool_calls = await service.get_events(session.id, types=[EventType.TOOL_CALL])
    assert tool_calls == []


async def test_performance_read(service: SessionService, agent_id: UUID) -> None:
    """Emit 10k events, read the tail of 100, p99 latency < 50ms.

    Tight target — this confirms the idx_events_session_seq index is
    actually doing work. Skips if the host is too slow to emit 10k
    events in a reasonable time (treated as infrastructure, not a bug).
    """
    session = await service.create_session(agent_id)

    total = 10_000
    for i in range(total):
        await service.emit_event(
            session.id,
            event_id=uuid4(),
            event_type=EventType.USER_MESSAGE,
            payload={"i": i},
        )

    # Warmup — first read may prime caches / plan.
    await service.get_events(session.id, start=total - 99, end=total, limit=100)

    iterations = 50
    samples: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        events = await service.get_events(session.id, start=total - 99, end=total, limit=100)
        samples.append((time.perf_counter() - t0) * 1000.0)
        assert len(events) == 100
        assert events[0].sequence == total - 99
        assert events[-1].sequence == total

    samples.sort()
    p99_index = max(0, round(0.99 * iterations) - 1)
    p99 = samples[p99_index]
    median = statistics.median(samples)
    print(f"get_events tail-100/10k p50={median:.2f}ms p99={p99:.2f}ms")
    assert p99 < 50.0, f"p99={p99:.2f}ms exceeds 50ms target"


async def test_crash_recovery(service: SessionService, agent_id: UUID) -> None:
    """Simulate harness crash: dispose service mid-session, resume on new instance.

    Validates principle #3 (statelessness) at the service boundary.
    A second SessionService pointed at the same database picks up
    last_sequence = 50 and continues from 51 with no gaps.
    """
    session = await service.create_session(agent_id)

    # First harness — emits 50 events then "crashes" (pool disposed).
    for i in range(50):
        await _emit_user(service, session.id, f"pre-crash-{i}")
    assert (await service.wake(session.id)).last_sequence == 50

    await service.close()  # drops connection pool, simulates harness death

    # Second harness — fresh SessionService, same DB.
    recovered = SessionService(_test_db_url_or_skip())
    try:
        woken = await recovered.wake(session.id)
        assert woken.last_sequence == 50

        for i in range(50):
            await recovered.emit_event(
                session.id,
                event_id=uuid4(),
                event_type=EventType.USER_MESSAGE,
                payload={"i": i, "phase": "post-crash"},
            )

        all_events = await recovered.get_events(session.id, limit=200)
        assert len(all_events) == 100
        assert [e.sequence for e in all_events] == list(range(1, 101))
    finally:
        await recovered.close()


def _test_db_url_or_skip() -> str:
    import os

    url = os.getenv("TENAME_TEST_DATABASE_URL")
    if not url:
        pytest.skip("TENAME_TEST_DATABASE_URL not set")
    return url
