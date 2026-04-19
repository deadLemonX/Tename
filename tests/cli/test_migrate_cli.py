"""CLI tests for `tename migrate`.

Unit tests cover the config-resolution and bundled-migrations-path
plumbing. The end-to-end test is `postgres`-marked and applies the
migrations to a real database, which is the only way to verify that
alembic can actually find the wheel's `env.py` + versions directory
from a `Config` object built programmatically.
"""

from __future__ import annotations

import argparse
import io
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tename.cli.main import build_parser, main
from tename.cli.migrate_commands import (
    build_alembic_config,
    bundled_migrations_path,
    cmd_migrate,
)

TEST_DB_ENV = "TENAME_TEST_DATABASE_URL"


def _migrate_args(**extra: object) -> argparse.Namespace:
    base: dict[str, object] = {"database_url": None, "revision": "head"}
    base.update(extra)
    return argparse.Namespace(**base)


def test_bundled_migrations_path_resolves() -> None:
    path = bundled_migrations_path()
    assert path.is_dir()
    assert (path / "env.py").is_file()
    versions = path / "versions"
    assert versions.is_dir()
    assert any(versions.glob("*_initial_schema.py"))


def test_build_alembic_config_sets_expected_options() -> None:
    cfg = build_alembic_config("postgresql+psycopg://user:pass@host/db")
    assert cfg.get_main_option("sqlalchemy.url") == "postgresql+psycopg://user:pass@host/db"
    script_location = cfg.get_main_option("script_location")
    assert script_location is not None
    assert Path(script_location).is_dir()


def test_cmd_migrate_errors_when_no_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TENAME_DATABASE_URL", raising=False)
    stderr = io.StringIO()
    rc = cmd_migrate(_migrate_args(), stderr=stderr)
    assert rc == 2
    assert "TENAME_DATABASE_URL" in stderr.getvalue()


def test_cmd_migrate_picks_up_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no --database-url is passed, the env var wins — but only if
    it's non-empty. An empty env var should be treated as unset."""
    monkeypatch.setenv("TENAME_DATABASE_URL", "")
    stderr = io.StringIO()
    rc = cmd_migrate(_migrate_args(), stderr=stderr)
    assert rc == 2
    assert "TENAME_DATABASE_URL" in stderr.getvalue()


def test_migrate_wired_into_parser() -> None:
    parser = build_parser()
    args = parser.parse_args(["migrate", "--database-url", "postgresql://x", "--revision", "head"])
    assert args.command == "migrate"
    assert args.database_url == "postgresql://x"
    assert args.revision == "head"
    assert callable(args.func)


# ---------------------------------------------------------------------------
# Integration: actually apply the migrations against a real database.
# ---------------------------------------------------------------------------


def _test_db_url() -> str | None:
    return os.getenv(TEST_DB_ENV)


@pytest.fixture
def clean_migration_db() -> Iterator[str]:
    """Drop every table in the test DB so the migration runs from scratch."""
    import asyncio

    url = _test_db_url()
    if not url:
        pytest.skip(f"{TEST_DB_ENV} not set; integration test requires live Postgres")

    async def _reset() -> None:
        eng = create_async_engine(url, future=True)
        try:
            async with eng.begin() as conn:
                await conn.execute(text("DROP SCHEMA public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))
        finally:
            await eng.dispose()

    asyncio.run(_reset())
    yield url


@pytest.mark.postgres
def test_cmd_migrate_applies_schema_end_to_end(
    clean_migration_db: str,
) -> None:
    """Run `cmd_migrate` against a freshly-wiped DB and verify the
    expected tables exist afterward. This is the closest equivalent to
    a real user's `pip install tename && tename migrate` flow."""
    import asyncio

    stdout = io.StringIO()
    rc = cmd_migrate(_migrate_args(database_url=clean_migration_db), stdout=stdout)
    assert rc == 0
    assert "migrations applied" in stdout.getvalue()

    async def _verify() -> list[str]:
        eng = create_async_engine(clean_migration_db, future=True)
        try:
            async with eng.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' ORDER BY tablename"
                    )
                )
                return [row[0] for row in result.all()]
        finally:
            await eng.dispose()

    tables = asyncio.run(_verify())
    assert "agents" in tables
    assert "sessions" in tables
    assert "events" in tables
    assert "alembic_version" in tables


@pytest.mark.postgres
def test_main_migrate_end_to_end(clean_migration_db: str) -> None:
    """Drive the full `main(argv)` path, not just `cmd_migrate`."""
    rc = main(["migrate", "--database-url", clean_migration_db])
    assert rc == 0
