"""Crypto key tables: space_keys and object_keys (wrapped keys only).

Revision ID: 8_1_keys
Revises: 7_1_audit
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8_1_keys"
down_revision: str | Sequence[str] | None = "7_1_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "space_keys",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=False),
        sa.Column("kek_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "octet_length(wrapped_key) = 61", name=op.f("ck_space_keys_wrapped_key_length")
        ),
        sa.CheckConstraint("kek_version >= 1", name=op.f("ck_space_keys_kek_version_positive")),
        sa.PrimaryKeyConstraint("space_id", name=op.f("pk_space_keys")),
    )
    op.create_table(
        "object_keys",
        sa.Column("object_type", sa.Text(), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "octet_length(wrapped_key) = 61", name=op.f("ck_object_keys_wrapped_key_length")
        ),
        sa.CheckConstraint(
            r"object_type ~ '^[a-z][a-z0-9_]{0,31}$'", name=op.f("ck_object_keys_object_type")
        ),
        sa.PrimaryKeyConstraint("object_type", "object_id", name=op.f("pk_object_keys")),
    )
    op.create_index("ix_object_keys_space_id", "object_keys", ["space_id"])
    # Default privileges gave app_rw UPDATE too; only rotation (app_migrator) updates.
    op.execute("REVOKE ALL ON space_keys, object_keys FROM app_rw")
    op.execute("GRANT SELECT, INSERT, DELETE ON space_keys, object_keys TO app_rw")


def downgrade() -> None:
    op.drop_index("ix_object_keys_space_id", table_name="object_keys")
    op.drop_table("object_keys")
    op.drop_table("space_keys")
