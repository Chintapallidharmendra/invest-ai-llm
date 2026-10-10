"""Conversations, their selected documents, messages, message tables and sources.

Every table gets owner-only space RLS (``owner_column="owner_user_id"``): a conversation
and everything under it is visible only to its owner, and only while they belong to its
space. Children carry the parent's ``space_id`` and ``owner_user_id`` through composite
foreign keys, so they can't diverge from it.

Revision ID: 3_1_conversations
Revises: 8_2_spaces
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.spaces.rls import disable_space_rls, enable_space_rls

revision: str = "3_1_conversations"
down_revision: str | Sequence[str] | None = "8_2_spaces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUMS = {
    "message_role": ("user", "assistant"),
    "message_status": ("pending", "streaming", "complete", "declined", "failed", "cancelled"),
}
TABLES = (
    "conversations",
    "conversation_documents",
    "messages",
    "message_tables",
    "message_sources",
)
PARENT_KEY = ("id", "space_id", "owner_user_id")


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def _ts(name: str, *, nullable: bool = False, default: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()") if default else None,
        nullable=nullable,
    )


def _id() -> sa.Column[object]:
    return sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False)


def _owned() -> list[sa.Column[object]]:
    return [
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("owner_user_id", sa.UUID(), nullable=False),
    ]


def _child_of(table: str, parent: str, column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column, "space_id", "owner_user_id"],
        [f"{parent}.{c}" for c in PARENT_KEY],
        name=op.f(f"fk_{table}_{column}_{parent}"),
        ondelete="CASCADE",
    )


def upgrade() -> None:
    bind = op.get_bind()
    for name in ENUMS:
        _enum(name).create(bind, checkfirst=False)

    op.create_table(
        "conversations",
        _id(),
        *_owned(),
        sa.Column("title_enc", sa.LargeBinary(), nullable=True),
        sa.Column("summary_enc", sa.LargeBinary(), nullable=True),
        sa.Column("summary_upto_message_id", sa.UUID(), nullable=True),
        _ts("expires_at"),
        _ts("deleted_at", nullable=True),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["spaces.id"],
            name=op.f("fk_conversations_space_id_spaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_conversations_owner_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        sa.UniqueConstraint(*PARENT_KEY, name="uq_conversations_id_space_owner"),
    )
    op.create_index(
        "ix_conversations_owner_updated", "conversations", ["owner_user_id", "updated_at", "id"]
    )
    op.create_index("ix_conversations_space_id", "conversations", ["space_id"])
    op.create_index("ix_conversations_expires_at", "conversations", ["expires_at"])

    op.create_table(
        "conversation_documents",
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        *_owned(),
        _ts("added_at", default=True),
        _child_of("conversation_documents", "conversations", "conversation_id"),
        sa.PrimaryKeyConstraint(
            "conversation_id", "document_id", name=op.f("pk_conversation_documents")
        ),
    )
    op.create_index(
        "ix_conversation_documents_document_id", "conversation_documents", ["document_id"]
    )

    op.create_table(
        "messages",
        _id(),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        *_owned(),
        sa.Column("role", _enum("message_role"), nullable=False),
        sa.Column("content_enc", sa.LargeBinary(), nullable=True),
        sa.Column("status", _enum("message_status"), nullable=False),
        sa.Column("decline_category", sa.Text(), nullable=True),
        sa.Column("model_name", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.UUID(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("token_usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.CheckConstraint(
            "decline_category IS NULL OR decline_category ~ '^[a-z][a-z0-9_]{0,63}$'",
            name=op.f("ck_messages_decline_category_format"),
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0", name=op.f("ck_messages_latency_non_negative")
        ),
        _child_of("messages", "conversations", "conversation_id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint(*PARENT_KEY, name="uq_messages_id_space_owner"),
    )
    op.create_index(
        "ix_messages_conversation_created", "messages", ["conversation_id", "created_at", "id"]
    )

    op.create_table(
        "message_tables",
        _id(),
        sa.Column("message_id", sa.UUID(), nullable=False),
        *_owned(),
        sa.Column("position", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("title_enc", sa.LargeBinary(), nullable=True),
        sa.Column("columns_enc", sa.LargeBinary(), nullable=False),
        sa.Column("rows_enc", sa.LargeBinary(), nullable=False),
        sa.Column("source_locators", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        _ts("created_at", default=True),
        _child_of("message_tables", "messages", "message_id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_tables")),
    )
    op.create_index("ix_message_tables_message_id", "message_tables", ["message_id"])

    op.create_table(
        "message_sources",
        _id(),
        sa.Column("message_id", sa.UUID(), nullable=False),
        *_owned(),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=True),
        sa.Column("locator", sa.Text(), nullable=True),
        _ts("created_at", default=True),
        sa.CheckConstraint(
            "kind ~ '^[a-z][a-z0-9_]{0,31}$'", name=op.f("ck_message_sources_kind_format")
        ),
        sa.CheckConstraint(
            "locator IS NULL OR char_length(locator) <= 200",
            name=op.f("ck_message_sources_locator_length"),
        ),
        _child_of("message_sources", "messages", "message_id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_sources")),
    )
    op.create_index("ix_message_sources_message_id", "message_sources", ["message_id"])

    for table in TABLES:
        enable_space_rls(op, table, owner_column="owner_user_id")


def downgrade() -> None:
    for table in reversed(TABLES):
        disable_space_rls(op, table)
        op.drop_table(table)
    bind = op.get_bind()
    for name in reversed(list(ENUMS)):
        _enum(name).drop(bind, checkfirst=False)
