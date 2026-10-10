"""Conversation tables (ADR-023, ADR-028): ``conversations``, ``conversation_documents``,
``messages``, ``message_tables``, ``message_sources``.

Every table has ``space_id`` and ``owner_user_id`` and **owner-only** RLS
(``enable_space_rls(..., owner_column="owner_user_id")``), so a conversation stays
private to its creator even inside a shared workspace. Children repeat the parent's
``space_id`` and ``owner_user_id``; composite foreign keys make them equal to the
parent's, so a child can never sit in another space or belong to another owner.

Content is encrypted under the conversation's object key ``("conversation", id)``; see
:mod:`app.chat.repository` for the per-row field names (the AAD). No plaintext content
column exists: locators (``p.12``, ``Sheet1!C4``) and model metadata are not content.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageStatus(StrEnum):
    PENDING = "pending"  # created, nothing generated yet
    STREAMING = "streaming"
    COMPLETE = "complete"
    DECLINED = "declined"  # refused by a guardrail (see decline_category)
    FAILED = "failed"
    CANCELLED = "cancelled"


def _pg_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        create_type=False,
    )


message_role = _pg_enum(MessageRole, "message_role")
message_status = _pg_enum(MessageStatus, "message_status")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()"))


def _uuid() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True))


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=text("now()"))


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    space_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE")
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    title_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    summary_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    # The newest message the summary covers (context condensation, 3.2).
    summary_upto_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (
        # The targets of the children's composite foreign keys.
        UniqueConstraint("id", "space_id", "owner_user_id", name="uq_conversations_id_space_owner"),
        Index("ix_conversations_owner_updated", "owner_user_id", "updated_at", "id"),
        Index("ix_conversations_space_id", "space_id"),
        Index("ix_conversations_expires_at", "expires_at"),
    )


class ConversationDocument(Base):
    """The conversation's selected set: documents of its space (FR-055)."""

    __tablename__ = "conversation_documents"

    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    # FK to documents is added with the documents table (9.1).
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    space_id: Mapped[uuid.UUID] = _uuid()
    owner_user_id: Mapped[uuid.UUID] = _uuid()
    added_at: Mapped[datetime] = _now()

    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "space_id", "owner_user_id"],
            ["conversations.id", "conversations.space_id", "conversations.owner_user_id"],
            ondelete="CASCADE",
        ),
        Index("ix_conversation_documents_document_id", "document_id"),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = _uuid()
    space_id: Mapped[uuid.UUID] = _uuid()
    owner_user_id: Mapped[uuid.UUID] = _uuid()
    role: Mapped[MessageRole] = mapped_column(message_role)
    content_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    status: Mapped[MessageStatus] = mapped_column(message_status)
    decline_category: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "space_id", "owner_user_id"],
            ["conversations.id", "conversations.space_id", "conversations.owner_user_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "space_id", "owner_user_id", name="uq_messages_id_space_owner"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at", "id"),
        # Metadata only: short machine values, never text a user or model wrote.
        CheckConstraint(
            "decline_category IS NULL OR decline_category ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="decline_category_format",
        ),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="latency_non_negative"),
    )


class MessageTable(Base):
    """A table in an assistant message: title, columns and rows encrypted."""

    __tablename__ = "message_tables"

    id: Mapped[uuid.UUID] = _uuid_pk()
    message_id: Mapped[uuid.UUID] = _uuid()
    space_id: Mapped[uuid.UUID] = _uuid()
    owner_user_id: Mapped[uuid.UUID] = _uuid()
    position: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    title_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    columns_enc: Mapped[bytes] = mapped_column(LargeBinary)
    rows_enc: Mapped[bytes] = mapped_column(LargeBinary)
    # Where each value came from (document IDs and locators): not content.
    source_locators: Mapped[list[Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        ForeignKeyConstraint(
            ["message_id", "space_id", "owner_user_id"],
            ["messages.id", "messages.space_id", "messages.owner_user_id"],
            ondelete="CASCADE",
        ),
        Index("ix_message_tables_message_id", "message_id"),
    )


class MessageSource(Base):
    """A source cited by an assistant message (document + locator)."""

    __tablename__ = "message_sources"

    id: Mapped[uuid.UUID] = _uuid_pk()
    message_id: Mapped[uuid.UUID] = _uuid()
    space_id: Mapped[uuid.UUID] = _uuid()
    owner_user_id: Mapped[uuid.UUID] = _uuid()
    kind: Mapped[str] = mapped_column(Text)
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    locator: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        ForeignKeyConstraint(
            ["message_id", "space_id", "owner_user_id"],
            ["messages.id", "messages.space_id", "messages.owner_user_id"],
            ondelete="CASCADE",
        ),
        Index("ix_message_sources_message_id", "message_id"),
        CheckConstraint("kind ~ '^[a-z][a-z0-9_]{0,31}$'", name="kind_format"),
        CheckConstraint("locator IS NULL OR char_length(locator) <= 200", name="locator_length"),
    )
