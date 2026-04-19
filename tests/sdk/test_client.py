"""SDK integration tests.

These drive the full `AsyncTename` / `Tename` surface against real
Postgres + a fake model router. They verify the hello-world path (the
README snippet) works end-to-end without depending on a live Anthropic
key or Docker.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterable, Sequence
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tename.router.types import Message, ModelChunk, RouterProfile, ToolDef, text_delta
from tename.router.types import done_chunk as _done
from tename.sdk import AsyncTename, EventType, Tename

pytestmark = pytest.mark.postgres

TEST_DB_ENV = "TENAME_TEST_DATABASE_URL"


def _test_db_url() -> str | None:
    return os.getenv(TEST_DB_ENV)


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    url = _test_db_url()
    if not url:
        pytest.skip(f"{TEST_DB_ENV} not set; SDK integration tests require Postgres")
    eng = create_async_engine(url, future=True)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def clean_db(engine: AsyncEngine) -> AsyncIterator[None]:
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE events, sessions, agents RESTART IDENTITY CASCADE"))
    yield


class _ScriptedRouter:
    """Minimal ModelRouter stand-in for SDK tests."""

    def __init__(self, turns: Iterable[Iterable[ModelChunk]]) -> None:
        self._turns: list[list[ModelChunk]] = [list(t) for t in turns]
        self._n = 0

    async def complete(
        self,
        profile: RouterProfile,
        messages: Sequence[Message],
        tools: Sequence[ToolDef] | None = None,
    ) -> AsyncIterator[ModelChunk]:
        if self._n >= len(self._turns):
            return
        chunks = self._turns[self._n]
        self._n += 1
        for chunk in chunks:
            yield chunk


def _script_single_turn(text_value: str) -> _ScriptedRouter:
    return _ScriptedRouter(
        [
            [text_delta(text_value), _done()],
        ]
    )


async def test_async_hello_world(engine: AsyncEngine, clean_db: None, tmp_path: Path) -> None:
    url = _test_db_url()
    assert url is not None
    client = AsyncTename(
        database_url=url,
        vault_path=str(tmp_path / "v.enc"),
        vault_passphrase="pw",
        enable_sandbox=False,
    )
    try:
        # Inject a scripted router so the test doesn't need a live model.
        client.install_test_model_router(_script_single_turn("4"))

        agent = await client.agents.create(
            name="sdk-test",
            model="claude-opus-4-6",
            system_prompt="You are a math tutor.",
        )
        session = await client.sessions.create(agent_id=agent.id)

        collected = []
        async for event in session.send("What is 2+2?"):
            collected.append(event)

        types = [e.type for e in collected]
        assert EventType.USER_MESSAGE in types
        assert EventType.ASSISTANT_MESSAGE in types
        finals = [
            e
            for e in collected
            if e.type == EventType.ASSISTANT_MESSAGE and e.payload.get("is_complete")
        ]
        assert len(finals) == 1
        assert finals[0].payload["content"] == "4"
    finally:
        await client.close()


async def test_async_agent_crud(engine: AsyncEngine, clean_db: None, tmp_path: Path) -> None:
    url = _test_db_url()
    assert url is not None
    client = AsyncTename(
        database_url=url,
        vault_path=str(tmp_path / "v.enc"),
        vault_passphrase="pw",
        enable_sandbox=False,
    )
    try:
        agent = await client.agents.create(
            name="crud-test", model="claude-opus-4-6", tools=["python"]
        )
        fetched = await client.agents.get(agent.id)
        assert fetched.id == agent.id
        assert fetched.tools == ["python"]

        listed = await client.agents.list()
        assert any(a.id == agent.id for a in listed)

        await client.agents.delete(agent.id)
        from tename.sdk.errors import NotFoundError

        with pytest.raises(NotFoundError):
            await client.agents.get(agent.id)
    finally:
        await client.close()


def test_sync_hello_world(engine: AsyncEngine, clean_db: None, tmp_path: Path) -> None:
    """The `for event in session.send(...)` pattern from the README."""
    url = _test_db_url()
    assert url is not None
    client = Tename(
        database_url=url,
        vault_path=str(tmp_path / "v.enc"),
        vault_passphrase="pw",
        enable_sandbox=False,
    )
    try:
        # Swap in a scripted router so this test stays hermetic.
        client.install_test_model_router(_script_single_turn("42"))

        agent = client.agents.create(name="sync-test", model="claude-opus-4-6")
        session = client.sessions.create(agent_id=agent.id)

        events = list(session.send("hi"))
        finals = [
            e
            for e in events
            if e.type == EventType.ASSISTANT_MESSAGE and e.payload.get("is_complete")
        ]
        assert len(finals) == 1
        assert finals[0].payload["content"] == "42"
    finally:
        client.close()


def test_sync_context_manager_closes_cleanly(
    engine: AsyncEngine, clean_db: None, tmp_path: Path
) -> None:
    url = _test_db_url()
    assert url is not None
    with Tename(
        database_url=url,
        vault_path=str(tmp_path / "v.enc"),
        vault_passphrase="pw",
        enable_sandbox=False,
    ) as client:
        names = client.vault.list()
        assert names == []


def test_vault_client_roundtrip(engine: AsyncEngine, clean_db: None, tmp_path: Path) -> None:
    url = _test_db_url()
    assert url is not None
    with Tename(
        database_url=url,
        vault_path=str(tmp_path / "v.enc"),
        vault_passphrase="pw",
        enable_sandbox=False,
    ) as client:
        client.vault.set("api_key", "sk-123")
        assert client.vault.get("api_key") == "sk-123"
        assert "api_key" in client.vault.list()
        client.vault.remove("api_key")
        assert client.vault.list() == []
