"""Merge the two wave 5 heads: conversations (3.1) and outputs (7.2).

Both revised ``8_2_spaces`` in parallel; neither depends on the other, so the merge
has no operations. Owned by 7.2, which merged second (epics.md shared-file conventions).

Revision ID: 7_2_merge_heads
Revises: 3_1_conversations, 7_2_outputs
Create Date: 2026-10-10
"""

from collections.abc import Sequence

revision: str = "7_2_merge_heads"
down_revision: str | Sequence[str] | None = ("3_1_conversations", "7_2_outputs")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
