"""initial schema: agents, sessions, events.

Revision ID: 0001_initial_schema
Revises: 0000_baseline
Create Date: 2026-04-16

Creates the three core tables for the Session Service per
`docs/architecture/data-model.md`. All `tenant_id` columns carry a
default value so v0.1 can remain single-tenant without a schema change
when multi-tenancy is added.

The partial unique index on `sessions((metadata->>'request_id'))` is a
defense-in-depth guarantee for `create_session` idempotency. Lookups
in the store layer happen first; the index catches the race.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = "0000_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    """Create agents, sessions, events tables with indexes."""
    op.create_table(
        "agents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text(f"'{_DEFAULT_TENANT}'::uuid"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "framework",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'vanilla'"),
        ),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column(
            "tools",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("sandbox_recipe", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    op.create_table(
        "sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text(f"'{_DEFAULT_TENANT}'::uuid"),
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "last_sequence",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # Defense-in-depth idempotency guard. Partial so untagged sessions
    # don't collide on NULL.
    op.execute(
        "CREATE UNIQUE INDEX uq_sessions_request_id "
        "ON sessions (tenant_id, (metadata->>'request_id')) "
        "WHERE metadata ? 'request_id'"
    )

    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("session_id", "id", name="pk_events"),
        sa.UniqueConstraint("session_id", "sequence", name="uq_events_session_sequence"),
    )
    op.create_index(
        "idx_events_session_seq",
        "events",
        ["session_id", "sequence"],
    )
    op.create_index(
        "idx_events_session_type_seq",
        "events",
        ["session_id", "type", "sequence"],
    )
    op.create_index(
        "idx_events_session_created",
        "events",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    """Drop events, sessions, agents tables."""
    op.drop_index("idx_events_session_created", table_name="events")
    op.drop_index("idx_events_session_type_seq", table_name="events")
    op.drop_index("idx_events_session_seq", table_name="events")
    op.drop_table("events")
    op.execute("DROP INDEX IF EXISTS uq_sessions_request_id")
    op.drop_table("sessions")
    op.drop_table("agents")
