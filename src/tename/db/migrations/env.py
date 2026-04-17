"""Alembic migration environment for Tename.

Reads the database URL from the ``TENAME_DATABASE_URL`` environment
variable (falling back to ``sqlalchemy.url`` in ``alembic.ini``). Keeps
app and migration connection config in a single place.

Importing ``tename.sessions.schema`` registers its tables on the shared
metadata as a side effect, which is what Alembic's autogenerate needs.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from tename.db.schema import metadata
from tename.sessions import (
    schema as _sessions_schema,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.getenv("TENAME_DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = metadata


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (emits SQL to stdout)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
