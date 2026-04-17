"""Database layer for Tename.

Exposes an engine factory keyed on a SQLAlchemy URL so the backend is
pluggable. v0.1 ships with Postgres. SQLite support is planned but not
wired up yet; when it lands, the driver-specific handling will live here
and callers will not need to change.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine

MIGRATIONS_DIR: Path = Path(__file__).parent / "migrations"


def make_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Create a SQLAlchemy engine for the given URL.

    v0.1 supports `postgresql+psycopg://...`. SQLite support is deferred.
    """
    if not database_url:
        raise ValueError("database_url must be a non-empty SQLAlchemy URL")
    return create_engine(database_url, echo=echo, future=True)


__all__ = ["MIGRATIONS_DIR", "make_engine"]
