"""baseline (empty).

Revision ID: 0000_baseline
Revises:
Create Date: 2026-04-16

The real schema (agents, sessions, events) lands in S3. This revision
exists so the Alembic wiring is exercised end-to-end today.
"""

from collections.abc import Sequence

revision: str = "0000_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
