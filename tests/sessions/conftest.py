"""Shared fixtures for Session Service integration tests.

Integration tests talk to a live Postgres via `TENAME_TEST_DATABASE_URL`.
When that env var is unset (CI on a fresh check-out, for example) the
tests skip cleanly instead of erroring.

Every test gets a TRUNCATE of the session tables up-front so there is
no cross-test contamination, and a pre-seeded agent row so individual
tests don't have to repeat the boilerplate.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tename.sessions import SessionService

TEST_DB_ENV = "TENAME_TEST_DATABASE_URL"


def _test_db_url() -> str | None:
    return os.getenv(TEST_DB_ENV)


postgres = pytest.mark.postgres


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Raw async engine for setup/teardown SQL.

    Separate from the engine owned by SessionService so tests can
    TRUNCATE without racing the service's pool.
    """
    url = _test_db_url()
    if not url:
        pytest.skip(f"{TEST_DB_ENV} not set; integration tests require live Postgres")
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
async def agent_id(engine: AsyncEngine, clean_db: None) -> UUID:
    """Insert a minimal agent row and return its id."""
    agent_uuid = uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO agents (id, name, model) VALUES (:id, :name, :model)"),
            {"id": str(agent_uuid), "name": "test-agent", "model": "claude-opus-4-6"},
        )
    return agent_uuid


@pytest_asyncio.fixture
async def service() -> AsyncIterator[SessionService]:
    """SessionService wired to the test database."""
    url = _test_db_url()
    if not url:
        pytest.skip(f"{TEST_DB_ENV} not set; integration tests require live Postgres")
    svc = SessionService(url)
    try:
        yield svc
    finally:
        await svc.close()
