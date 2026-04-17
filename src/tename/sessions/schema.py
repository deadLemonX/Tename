"""SQLAlchemy Core table definitions for the Session Service.

Mirrors `docs/architecture/data-model.md`. We use Core (not the ORM) so
the store layer stays close to raw SQL and the Pydantic models in
`models.py` remain the single source of truth for application types.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    TIMESTAMP,
    Column,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from tename.db.schema import metadata

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000000"

# `JSONB` and `UUID` are Postgres-specific; we fall back to generic types
# on SQLite so future SQLite support can reuse the same schema.
_JSONB = JSONB().with_variant(JSON(), "sqlite")
_UUID = UUID(as_uuid=True).with_variant(Text(), "sqlite")


agents = Table(
    "agents",
    metadata,
    Column(
        "id",
        _UUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    ),
    Column(
        "tenant_id",
        _UUID,
        nullable=False,
        server_default=text(f"'{DEFAULT_TENANT_ID}'::uuid"),
    ),
    Column("name", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column(
        "framework",
        Text,
        nullable=False,
        server_default=text("'vanilla'"),
    ),
    Column("system_prompt", Text, nullable=True),
    Column(
        "tools",
        _JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    ),
    Column("sandbox_recipe", _JSONB, nullable=True),
    Column(
        "created_at",
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    ),
)


sessions = Table(
    "sessions",
    metadata,
    Column(
        "id",
        _UUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    ),
    Column(
        "tenant_id",
        _UUID,
        nullable=False,
        server_default=text(f"'{DEFAULT_TENANT_ID}'::uuid"),
    ),
    Column(
        "agent_id",
        _UUID,
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "status",
        Text,
        nullable=False,
        server_default=text("'active'"),
    ),
    Column(
        "last_sequence",
        Integer,
        nullable=False,
        server_default=text("0"),
    ),
    Column(
        "metadata",
        _JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    ),
    Column(
        "created_at",
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    ),
    Column(
        "updated_at",
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    ),
)


events = Table(
    "events",
    metadata,
    Column("id", _UUID, nullable=False),
    Column(
        "session_id",
        _UUID,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("sequence", Integer, nullable=False),
    Column("type", Text, nullable=False),
    Column("payload", _JSONB, nullable=False),
    Column(
        "created_at",
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    ),
    PrimaryKeyConstraint("session_id", "id", name="pk_events"),
    UniqueConstraint("session_id", "sequence", name="uq_events_session_sequence"),
    Index("idx_events_session_seq", "session_id", "sequence"),
    Index(
        "idx_events_session_type_seq",
        "session_id",
        "type",
        "sequence",
    ),
    Index("idx_events_session_created", "session_id", "created_at"),
)


__all__ = ["DEFAULT_TENANT_ID", "agents", "events", "sessions"]
