"""Shared SQLAlchemy metadata for the Tename runtime.

All table definitions across the codebase register against this single
`MetaData` so Alembic's autogenerate can see the complete schema in one
place. Individual modules (e.g. `tename.sessions.schema`) import this
metadata and hang their tables off it.

Keeping metadata here (instead of the owning module) avoids import-order
puzzles in the migration env: `env.py` imports this module, then imports
the side-effect schema modules, and the metadata is guaranteed complete.
"""

from __future__ import annotations

from sqlalchemy import MetaData

metadata: MetaData = MetaData()

__all__ = ["metadata"]
