"""Shared fixtures for Harness Runtime tests.

Provides:

- `FakeModelRouter`: scripted stand-in for `ModelRouter`. Each entry in
  `turns` is a list of `ModelChunk`s yielded in order by one `complete()`
  call, mirroring the real router's "text / tool_call events → usage →
  done" shape. Captures every call's profile / messages / tools so tests
  can assert on them.

- Postgres-backed fixtures (`engine`, `clean_db`, `service`,
  `agent_with_prompt`, `agent_no_prompt`) for integration tests. Gated on
  `TENAME_TEST_DATABASE_URL`; absent → tests skip cleanly.

These mirror the shape used by the Session Service integration tests so
fixtures are familiar across the suite.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterable, Sequence
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tename.router.types import Message, ModelChunk, RouterProfile, ToolDef
from tename.sessions import SessionService

TEST_DB_ENV = "TENAME_TEST_DATABASE_URL"


def _test_db_url() -> str | None:
    return os.getenv(TEST_DB_ENV)


class _RouterCall:
    """Snapshot of a single `FakeModelRouter.complete()` invocation."""

    __slots__ = ("messages", "profile", "tools")

    def __init__(
        self,
        *,
        profile: RouterProfile,
        messages: list[Message],
        tools: list[ToolDef],
    ) -> None:
        self.profile = profile
        self.messages = messages
        self.tools = tools


class FakeModelRouter:
    """Scripted stand-in for `ModelRouter`.

    Construct with a list of "turns"; each turn is itself a list of
    `ModelChunk`s to yield in order. `complete()` consumes one turn per
    call. If the script is exhausted, further calls yield nothing — the
    harness will see an empty stream and stop.

    `raise_on_turn` / `raise_after_n_chunks` let crash-recovery tests
    abort streaming partway through a specific turn.
    """

    def __init__(
        self,
        turns: Iterable[Iterable[ModelChunk]],
        *,
        raise_on_turn: int | None = None,
        raise_after_n_chunks: int = 0,
        raise_exc: BaseException | None = None,
    ) -> None:
        self._turns: list[list[ModelChunk]] = [list(t) for t in turns]
        self.calls: list[_RouterCall] = []
        self._raise_on_turn = raise_on_turn
        self._raise_after_n = raise_after_n_chunks
        self._raise_exc = raise_exc

    async def complete(
        self,
        profile: RouterProfile,
        messages: Sequence[Message],
        tools: Sequence[ToolDef] | None = None,
    ) -> AsyncIterator[ModelChunk]:
        call_index = len(self.calls)
        self.calls.append(
            _RouterCall(profile=profile, messages=list(messages), tools=list(tools or []))
        )
        if call_index >= len(self._turns):
            return
        chunks = self._turns[call_index]
        for i, chunk in enumerate(chunks):
            if (
                self._raise_on_turn is not None
                and call_index == self._raise_on_turn
                and i >= self._raise_after_n
                and self._raise_exc is not None
            ):
                raise self._raise_exc
            yield chunk


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Raw async engine for setup/teardown SQL."""
    url = _test_db_url()
    if not url:
        pytest.skip(f"{TEST_DB_ENV} not set; harness integration tests require live Postgres")
    eng = create_async_engine(url, future=True)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def clean_db(engine: AsyncEngine) -> AsyncIterator[None]:
    """Reset session-service tables before each test."""
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE events, sessions, agents RESTART IDENTITY CASCADE"))
    yield


@pytest_asyncio.fixture
async def service() -> AsyncIterator[SessionService]:
    """SessionService wired to the test database."""
    url = _test_db_url()
    if not url:
        pytest.skip(f"{TEST_DB_ENV} not set; harness integration tests require live Postgres")
    svc = SessionService(url)
    try:
        yield svc
    finally:
        await svc.close()


@pytest_asyncio.fixture
async def agent_with_prompt(engine: AsyncEngine, clean_db: None) -> UUID:
    """Insert an agent row with a system prompt."""
    agent_uuid = uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agents (id, name, model, framework, system_prompt) "
                "VALUES (:id, :name, :model, :framework, :system_prompt)"
            ),
            {
                "id": str(agent_uuid),
                "name": "test-harness-agent",
                "model": "claude-opus-4-6",
                "framework": "vanilla",
                "system_prompt": "You are a helpful assistant.",
            },
        )
    return agent_uuid


@pytest_asyncio.fixture
async def agent_no_prompt(engine: AsyncEngine, clean_db: None) -> UUID:
    """Insert an agent row with no system prompt."""
    agent_uuid = uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agents (id, name, model, framework, system_prompt) "
                "VALUES (:id, :name, :model, :framework, NULL)"
            ),
            {
                "id": str(agent_uuid),
                "name": "test-harness-agent",
                "model": "claude-opus-4-6",
                "framework": "vanilla",
            },
        )
    return agent_uuid


def has_env(name: str) -> bool:
    return bool(os.getenv(name))


__all__ = ["FakeModelRouter", "agent_no_prompt", "agent_with_prompt", "has_env"]
