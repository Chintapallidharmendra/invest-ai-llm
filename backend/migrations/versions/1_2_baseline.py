"""Baseline: extensions vector (pgvector) and citext.

Revision ID: 1_2_baseline
Revises:
Create Date: 2026-10-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1_2_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS citext")
    op.execute("DROP EXTENSION IF EXISTS vector")
