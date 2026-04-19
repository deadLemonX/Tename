"""`tename migrate` subcommand.

Applies the alembic migrations bundled inside the wheel against the
database URL from `--database-url` or `TENAME_DATABASE_URL`. This is
the "I `pip install`ed tename, I have my own Postgres, just apply the
schema" path — no repo checkout required.

The migrations themselves (`tename/db/migrations/env.py` + the
`versions/` directory) ship inside the wheel; we locate them via
`importlib.resources` and hand the filesystem path to alembic's
`Config` programmatically.
"""

from __future__ import annotations

import argparse
import os
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any, TextIO

from alembic import command
from alembic.config import Config

from tename.sdk._config import DATABASE_URL_ENV


def _out(stream: TextIO | None) -> TextIO:
    return stream if stream is not None else sys.stdout


def _err(stream: TextIO | None) -> TextIO:
    return stream if stream is not None else sys.stderr


def bundled_migrations_path() -> Path:
    """Return a filesystem path to the wheel's `tename/db/migrations/` directory.

    Standard pip installs unpack wheels to `site-packages/`, so the
    `Traversable` returned by `importlib.resources.files` points at a
    real filesystem path. Zipped imports would need `as_file()` to
    materialize the directory to a tempdir, but tename's wheel is not
    published as a zipimport target — if that ever changes the
    `is_dir()` check below will raise a clear error rather than
    failing deep inside alembic.
    """
    traversable = files("tename.db").joinpath("migrations")
    path = Path(str(traversable))
    if not path.is_dir():
        raise RuntimeError(
            f"bundled migrations not found at {path!r}; reinstall tename "
            "(if you're running from a zipped import you'll need to "
            "clone the repo and use `make migrate` instead)"
        )
    return path


def build_alembic_config(database_url: str) -> Config:
    """Construct an alembic `Config` pointed at the wheel's migrations.

    The bundled `env.py` honors `TENAME_DATABASE_URL` on its own, but
    we set `sqlalchemy.url` explicitly too so the value the CLI
    accepted is what gets applied (avoids a subtle precedence mismatch
    when the caller passed `--database-url` without exporting the env
    var).
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(bundled_migrations_path()))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def cmd_migrate(
    args: argparse.Namespace,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    database_url: str | None = getattr(args, "database_url", None) or os.environ.get(
        DATABASE_URL_ENV
    )
    if not database_url:
        print(
            f"error: database URL is required (pass --database-url or set {DATABASE_URL_ENV})",
            file=_err(stderr),
        )
        return 2

    revision: str = getattr(args, "revision", None) or "head"

    cfg = build_alembic_config(database_url)
    command.upgrade(cfg, revision)
    print(f"migrations applied: -> {revision}", file=_out(stdout))
    return 0


def add_migrate_subparser(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "migrate",
        help="Apply database migrations bundled with Tename.",
        description=(
            "Run alembic against the database URL from --database-url or "
            "TENAME_DATABASE_URL, using the migrations bundled inside the "
            "installed tename wheel. No repo checkout required."
        ),
    )
    p.add_argument(
        "--database-url",
        default=None,
        help=(
            "SQLAlchemy URL. Overrides the TENAME_DATABASE_URL env var "
            "for this invocation."
        ),
    )
    p.add_argument(
        "--revision",
        default="head",
        help="Target revision (default: head).",
    )
    p.set_defaults(func=cmd_migrate)


__all__ = [
    "add_migrate_subparser",
    "build_alembic_config",
    "bundled_migrations_path",
    "cmd_migrate",
]
