"""Outputs (generated files) and their download tokens.

Both tables get owner-only space RLS: an output and its links are visible only to the
output's creator, and only while they still belong to its space.

Revision ID: 7_2_outputs
Revises: 8_2_spaces
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.spaces.rls import disable_space_rls, enable_space_rls

revision: str = "7_2_outputs"
down_revision: str | Sequence[str] | None = "8_2_spaces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUMS = {
    "output_kind": ("xlsx", "csv", "docx"),
    "output_origin": ("operations", "table_extract", "summary", "comparison"),
}


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def _ts(name: str, *, nullable: bool = False, default: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()") if default else None,
        nullable=nullable,
    )


def upgrade() -> None:
    bind = op.get_bind()
    for name in ENUMS:
        _enum(name).create(bind, checkfirst=False)

    op.create_table(
        "outputs",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("owner_user_id", sa.UUID(), nullable=False),
        sa.Column("kind", _enum("output_kind"), nullable=False),
        sa.Column("origin", _enum("output_origin"), nullable=False),
        sa.Column(
            "source_document_ids",
            postgresql.ARRAY(sa.UUID()),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column("version_of_id", sa.UUID(), nullable=True),
        sa.Column("title_enc", sa.LargeBinary(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("ops_log_enc", sa.LargeBinary(), nullable=True),
        _ts("expires_at"),
        _ts("deleted_at", nullable=True),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_outputs_size_non_negative")),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_outputs_content_hash_format")
        ),
        sa.CheckConstraint(
            "file_path ~ '^[0-9a-f-]{36}/[0-9a-f-]{36}$'",
            name=op.f("ck_outputs_file_path_format"),
        ),
        sa.ForeignKeyConstraint(
            ["space_id"], ["spaces.id"], name=op.f("fk_outputs_space_id_spaces"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_outputs_owner_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["version_of_id"],
            ["outputs.id"],
            name=op.f("fk_outputs_version_of_id_outputs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outputs")),
        sa.UniqueConstraint("id", "space_id", "owner_user_id", name="uq_outputs_id_space_owner"),
    )
    op.create_index(
        "ix_outputs_owner_space_created", "outputs", ["owner_user_id", "space_id", "created_at"]
    )
    op.create_index("ix_outputs_version_of_id", "outputs", ["version_of_id"])
    op.create_index("ix_outputs_expires_at", "outputs", ["expires_at"])

    op.create_table(
        "download_tokens",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("output_id", sa.UUID(), nullable=False),
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        _ts("expires_at"),
        sa.Column("used_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _ts("created_at", default=True),
        sa.CheckConstraint(
            "octet_length(token_hash) = 32", name=op.f("ck_download_tokens_token_hash_length")
        ),
        sa.CheckConstraint(
            "used_count >= 0", name=op.f("ck_download_tokens_used_count_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["output_id", "space_id", "user_id"],
            ["outputs.id", "outputs.space_id", "outputs.owner_user_id"],
            name=op.f("fk_download_tokens_output_id_outputs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_download_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_download_tokens_token_hash")),
    )
    op.create_index("ix_download_tokens_output_id", "download_tokens", ["output_id"])

    enable_space_rls(op, "outputs", owner_column="owner_user_id")
    enable_space_rls(op, "download_tokens", owner_column="user_id")


def downgrade() -> None:
    for table in ("download_tokens", "outputs"):
        disable_space_rls(op, table)
        op.drop_table(table)
    bind = op.get_bind()
    for name in reversed(list(ENUMS)):
        _enum(name).drop(bind, checkfirst=False)
