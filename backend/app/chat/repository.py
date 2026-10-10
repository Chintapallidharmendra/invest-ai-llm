"""Conversation persistence with field encryption (ADR-023, ADR-028).

Every function takes the caller's :class:`AccessContext` and a session **opened with
``transaction(context=ctx.rls_settings())``**, so owner-only RLS applies to every
statement::

    async with transaction(context=ctx.rls_settings()) as tx:
        conversation = await repository.get_conversation(tx, ctx, conversation_id)
        message = await repository.append_message(tx, ctx, conversation_id, ...)

Queries also filter on ``owner_user_id = ctx.user_id`` explicitly, so a session without
the RLS context sees nothing rather than everything.

**Encryption.** Each conversation has an object key ``("conversation", id)``. Every
encrypted value has its own field name, which is part of its AAD, so a ciphertext can't
be moved to another row or column without failing to decrypt:

- conversation: ``title``, ``summary``;
- message: ``message_<hex id>_content``;
- message table: ``table_<hex id>_title``, ``_columns``, ``_rows`` (columns and rows
  are JSON before encryption).

Deleting a conversation shreds its key first, so its ciphertext is unrecoverable even
before the rows are gone.
"""

import base64
import binascii
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import delete, insert, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer
from app.auth.service import correlation_id
from app.chat import selected_set
from app.chat.audit_events import ConversationCreated, ConversationDeleted
from app.chat.models import (
    Conversation,
    ConversationDocument,
    Message,
    MessageRole,
    MessageSource,
    MessageStatus,
    MessageTable,
)
from app.core.ids import new_uuid7
from app.core.timeutil import utcnow
from app.crypto.fields import decrypt_text, encrypt_field
from app.crypto.keys import KeyRef, create_object_key
from app.crypto.shred import shred_object
from app.spaces.access import AccessContext
from app.spaces.models import Space

OBJECT_TYPE: Final = "conversation"
TITLE_MAX_LENGTH: Final = 200
DEFAULT_PAGE_SIZE: Final = 30
MAX_PAGE_SIZE: Final = 100

type Cell = str | int | float | bool | None


class ConversationNotFoundError(Exception):
    """Not the caller's, not in one of their open spaces, deleted, or expired."""


class SpaceNotAccessibleError(Exception):
    """The caller isn't a member of that (open) space."""


class InvalidCursorError(ValueError):
    pass


# --- Views --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    id: uuid.UUID
    space_id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ConversationDetail(ConversationSummary):
    document_ids: tuple[uuid.UUID, ...]
    summary: str | None
    summary_upto_message_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class Page:
    items: list[ConversationSummary]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class TableView:
    id: uuid.UUID
    position: int
    title: str | None
    columns: list[str]
    rows: list[list[Cell]]
    source_locators: list[Any] | None


@dataclass(frozen=True, slots=True)
class SourceRef:
    kind: str  # e.g. "document", "sheet", "calculation"
    document_id: uuid.UUID | None = None
    locator: str | None = None  # e.g. "p.12", "Sheet1!C4"


@dataclass(frozen=True, slots=True)
class MessageView:
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: MessageRole
    status: MessageStatus
    content: str | None
    decline_category: str | None
    model_name: str | None
    prompt_version: str | None
    correlation_id: uuid.UUID | None
    latency_ms: int | None
    token_usage: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    tables: tuple[TableView, ...] = ()
    sources: tuple[SourceRef, ...] = ()


# --- Keys and fields ---------------------------------------------------------------------


def key_ref(conversation: Conversation | Message) -> KeyRef:
    conversation_id = (
        conversation.id if isinstance(conversation, Conversation) else conversation.conversation_id
    )
    return KeyRef(conversation.space_id, OBJECT_TYPE, conversation_id)


def message_field(message_id: uuid.UUID) -> str:
    return f"message_{message_id.hex}_content"


def table_field(table_id: uuid.UUID, part: str) -> str:
    return f"table_{table_id.hex}_{part}"


def clean_title(title: str) -> str:
    """Trimmed, 1-200 characters, else ``ValueError``."""
    cleaned = " ".join(title.split())
    if not 1 <= len(cleaned) <= TITLE_MAX_LENGTH:
        raise ValueError("title must be 1-200 characters")
    return cleaned


async def _encrypt(tx: AsyncSession, ref: KeyRef, field: str, value: str | bytes) -> bytes:
    return await encrypt_field(ref, field, value, executor=tx)


async def _decrypt(tx: AsyncSession, ref: KeyRef, field: str, value: bytes | None) -> str | None:
    if value is None:
        return None
    return await decrypt_text(ref, field, value, executor=tx)


# --- Conversations ------------------------------------------------------------------------


def _visible(ctx: AccessContext, now: datetime) -> list[Any]:
    return [
        Conversation.owner_user_id == ctx.user_id,
        Conversation.deleted_at.is_(None),
        Conversation.expires_at > now,
    ]


async def _load(
    tx: AsyncSession,
    ctx: AccessContext,
    conversation_id: uuid.UUID,
    now: datetime,
    *,
    lock: bool = False,
) -> Conversation:
    stmt = select(Conversation).where(Conversation.id == conversation_id, *_visible(ctx, now))
    if lock:
        stmt = stmt.with_for_update()
    conversation = (
        await tx.execute(stmt.execution_options(populate_existing=True))
    ).scalar_one_or_none()
    if conversation is None or not ctx.can_access(conversation.space_id):
        raise ConversationNotFoundError
    return conversation


async def create_conversation(
    tx: AsyncSession, ctx: AccessContext, space_id: uuid.UUID, *, now: datetime | None = None
) -> ConversationDetail:
    """A new, untitled conversation in ``space_id``, expiring after the space's retention
    period (FR-052: counted from creation)."""
    now = now or utcnow()
    if not ctx.can_access(space_id):
        raise SpaceNotAccessibleError
    retention = (
        await tx.execute(
            select(Space.retention_days).where(Space.id == space_id, Space.closed_at.is_(None))
        )
    ).scalar_one_or_none()
    if retention is None:
        raise SpaceNotAccessibleError
    conversation_id = new_uuid7()
    expires_at = now + timedelta(days=retention)
    await tx.execute(
        insert(Conversation).values(
            id=conversation_id,
            space_id=space_id,
            owner_user_id=ctx.user_id,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )
    )
    await create_object_key(tx, space_id, OBJECT_TYPE, conversation_id)
    await writer.record(
        ConversationCreated(conversation_id=conversation_id, space_id=space_id),
        actor_user_id=ctx.user_id,
        correlation_id=correlation_id(),
        target_type=OBJECT_TYPE,
        target_id=conversation_id,
        session=tx,
    )
    return ConversationDetail(
        id=conversation_id,
        space_id=space_id,
        title=None,
        created_at=now,
        updated_at=now,
        expires_at=expires_at,
        document_ids=(),
        summary=None,
        summary_upto_message_id=None,
    )


async def get_conversation(
    tx: AsyncSession, ctx: AccessContext, conversation_id: uuid.UUID, *, now: datetime | None = None
) -> ConversationDetail:
    conversation = await _load(tx, ctx, conversation_id, now or utcnow())
    ref = key_ref(conversation)
    documents = (
        await tx.execute(
            select(ConversationDocument.document_id)
            .where(ConversationDocument.conversation_id == conversation_id)
            .order_by(ConversationDocument.added_at, ConversationDocument.document_id)
        )
    ).scalars()
    return ConversationDetail(
        id=conversation.id,
        space_id=conversation.space_id,
        title=await _decrypt(tx, ref, "title", conversation.title_enc),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        expires_at=conversation.expires_at,
        document_ids=tuple(documents),
        summary=await _decrypt(tx, ref, "summary", conversation.summary_enc),
        summary_upto_message_id=conversation.summary_upto_message_id,
    )


def _encode_cursor(updated_at: datetime, conversation_id: uuid.UUID) -> str:
    raw = f"{updated_at.isoformat()}|{conversation_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        stamp, conversation_id = raw.split("|")
        return datetime.fromisoformat(stamp), uuid.UUID(conversation_id)
    except (ValueError, UnicodeDecodeError, binascii.Error):
        raise InvalidCursorError("invalid cursor") from None


async def list_conversations(
    tx: AsyncSession,
    ctx: AccessContext,
    *,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
    now: datetime | None = None,
) -> Page:
    """The caller's conversations in their open spaces, most recently active first."""
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    stmt = select(Conversation).where(
        *_visible(ctx, now or utcnow()), Conversation.space_id.in_(ctx.space_ids)
    )
    if cursor is not None:
        updated_at, conversation_id = _decode_cursor(cursor)
        stmt = stmt.where(
            tuple_(Conversation.updated_at, Conversation.id) < (updated_at, conversation_id)
        )
    rows = (
        (
            await tx.execute(
                stmt.order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(
                    limit + 1
                )
            )
        )
        .scalars()
        .all()
    )
    page = rows[:limit]
    items = [
        ConversationSummary(
            id=c.id,
            space_id=c.space_id,
            title=await _decrypt(tx, key_ref(c), "title", c.title_enc),
            created_at=c.created_at,
            updated_at=c.updated_at,
            expires_at=c.expires_at,
        )
        for c in page
    ]
    last = page[-1] if page and len(rows) > limit else None
    return Page(items, _encode_cursor(last.updated_at, last.id) if last else None)


async def rename_conversation(
    tx: AsyncSession,
    ctx: AccessContext,
    conversation_id: uuid.UUID,
    title: str,
    *,
    now: datetime | None = None,
) -> None:
    now = now or utcnow()
    conversation = await _load(tx, ctx, conversation_id, now, lock=True)
    encrypted = await _encrypt(tx, key_ref(conversation), "title", clean_title(title))
    await tx.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(title_enc=encrypted, updated_at=now)
        .execution_options(synchronize_session=False)
    )


async def delete_conversation(
    tx: AsyncSession, ctx: AccessContext, conversation_id: uuid.UUID, *, now: datetime | None = None
) -> None:
    """Permanent (FR-053): shred the key, then delete the conversation and its children."""
    conversation = await _load(tx, ctx, conversation_id, now or utcnow(), lock=True)
    await shred_object(tx, OBJECT_TYPE, conversation_id)
    await tx.execute(delete(Conversation).where(Conversation.id == conversation_id))
    await writer.record(
        ConversationDeleted(conversation_id=conversation_id, space_id=conversation.space_id),
        actor_user_id=ctx.user_id,
        correlation_id=correlation_id(),
        target_type=OBJECT_TYPE,
        target_id=conversation_id,
        session=tx,
    )


async def set_documents(
    tx: AsyncSession,
    ctx: AccessContext,
    conversation_id: uuid.UUID,
    document_ids: Sequence[uuid.UUID],
    *,
    now: datetime | None = None,
) -> tuple[uuid.UUID, ...]:
    """Replace the selected set. Every document must pass the registered resolver
    (``selected_set.DocumentNotFoundError`` otherwise); without one, only ``[]`` passes."""
    now = now or utcnow()
    conversation = await _load(tx, ctx, conversation_id, now, lock=True)
    unique = tuple(dict.fromkeys(document_ids))
    await selected_set.check(tx, ctx, conversation.space_id, unique)
    await tx.execute(
        delete(ConversationDocument).where(ConversationDocument.conversation_id == conversation_id)
    )
    if unique:
        await tx.execute(
            insert(ConversationDocument),
            [
                {
                    "conversation_id": conversation_id,
                    "document_id": document_id,
                    "space_id": conversation.space_id,
                    "owner_user_id": ctx.user_id,
                    "added_at": now,
                }
                for document_id in unique
            ],
        )
    await tx.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(updated_at=now)
        .execution_options(synchronize_session=False)
    )
    return unique


async def set_summary(
    tx: AsyncSession,
    ctx: AccessContext,
    conversation_id: uuid.UUID,
    summary: str,
    upto_message_id: uuid.UUID,
) -> None:
    """Store the condensed context up to (and including) ``upto_message_id``."""
    conversation = await _load(tx, ctx, conversation_id, utcnow(), lock=True)
    encrypted = await _encrypt(tx, key_ref(conversation), "summary", summary)
    await tx.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(summary_enc=encrypted, summary_upto_message_id=upto_message_id)
        .execution_options(synchronize_session=False)
    )


# --- Messages ---------------------------------------------------------------------------


async def append_message(
    tx: AsyncSession,
    ctx: AccessContext,
    conversation_id: uuid.UUID,
    *,
    role: MessageRole,
    content: str | None = None,
    status: MessageStatus = MessageStatus.COMPLETE,
    decline_category: str | None = None,
    model_name: str | None = None,
    prompt_version: str | None = None,
    message_correlation_id: uuid.UUID | None = None,
    latency_ms: int | None = None,
    token_usage: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> MessageView:
    now = now or utcnow()
    conversation = await _load(tx, ctx, conversation_id, now, lock=True)
    message_id = new_uuid7()
    ref = key_ref(conversation)
    content_enc = (
        None if content is None else await _encrypt(tx, ref, message_field(message_id), content)
    )
    await tx.execute(
        insert(Message).values(
            id=message_id,
            conversation_id=conversation_id,
            space_id=conversation.space_id,
            owner_user_id=ctx.user_id,
            role=role,
            content_enc=content_enc,
            status=status,
            decline_category=decline_category,
            model_name=model_name,
            prompt_version=prompt_version,
            correlation_id=message_correlation_id,
            latency_ms=latency_ms,
            token_usage=token_usage,
            created_at=now,
            updated_at=now,
        )
    )
    await tx.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(updated_at=now)
        .execution_options(synchronize_session=False)
    )
    return MessageView(
        id=message_id,
        conversation_id=conversation_id,
        role=role,
        status=status,
        content=content,
        decline_category=decline_category,
        model_name=model_name,
        prompt_version=prompt_version,
        correlation_id=message_correlation_id,
        latency_ms=latency_ms,
        token_usage=token_usage,
        created_at=now,
        updated_at=now,
    )


_UNSET: Final[Any] = object()


async def _load_message(tx: AsyncSession, ctx: AccessContext, message_id: uuid.UUID) -> Message:
    message = (
        await tx.execute(
            select(Message).where(Message.id == message_id, Message.owner_user_id == ctx.user_id)
        )
    ).scalar_one_or_none()
    if message is None or not ctx.can_access(message.space_id):
        raise ConversationNotFoundError
    await _load(tx, ctx, message.conversation_id, utcnow())  # still visible?
    return message


async def update_message(
    tx: AsyncSession,
    ctx: AccessContext,
    message_id: uuid.UUID,
    *,
    status: MessageStatus | None = None,
    content: str | None = _UNSET,
    decline_category: str | None = _UNSET,
    model_name: str | None = _UNSET,
    prompt_version: str | None = _UNSET,
    latency_ms: int | None = _UNSET,
    token_usage: dict[str, Any] | None = _UNSET,
    now: datetime | None = None,
) -> None:
    """Update status, content (re-encrypted) or metadata; omitted fields are kept."""
    message = await _load_message(tx, ctx, message_id)
    values: dict[str, Any] = {"updated_at": now or utcnow()}
    if status is not None:
        values["status"] = status
    if content is not _UNSET:
        values["content_enc"] = (
            None
            if content is None
            else await _encrypt(tx, key_ref(message), message_field(message_id), content)
        )
    for name, value in {
        "decline_category": decline_category,
        "model_name": model_name,
        "prompt_version": prompt_version,
        "latency_ms": latency_ms,
        "token_usage": token_usage,
    }.items():
        if value is not _UNSET:
            values[name] = value
    await tx.execute(
        update(Message)
        .where(Message.id == message_id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )


async def add_message_table(
    tx: AsyncSession,
    ctx: AccessContext,
    message_id: uuid.UUID,
    *,
    columns: Sequence[str],
    rows: Sequence[Sequence[Cell]],
    title: str | None = None,
    position: int = 0,
    source_locators: list[Any] | None = None,
) -> uuid.UUID:
    message = await _load_message(tx, ctx, message_id)
    ref = key_ref(message)
    table_id = new_uuid7()
    title_enc = (
        None if title is None else await _encrypt(tx, ref, table_field(table_id, "title"), title)
    )
    await tx.execute(
        insert(MessageTable).values(
            id=table_id,
            message_id=message_id,
            space_id=message.space_id,
            owner_user_id=ctx.user_id,
            position=position,
            title_enc=title_enc,
            columns_enc=await _encrypt(
                tx, ref, table_field(table_id, "columns"), json.dumps(list(columns))
            ),
            rows_enc=await _encrypt(
                tx, ref, table_field(table_id, "rows"), json.dumps([list(r) for r in rows])
            ),
            source_locators=source_locators,
        )
    )
    return table_id


async def add_message_sources(
    tx: AsyncSession, ctx: AccessContext, message_id: uuid.UUID, sources: Sequence[SourceRef]
) -> None:
    if not sources:
        return
    message = await _load_message(tx, ctx, message_id)
    await tx.execute(
        insert(MessageSource),
        [
            {
                "id": new_uuid7(),
                "message_id": message_id,
                "space_id": message.space_id,
                "owner_user_id": ctx.user_id,
                "kind": source.kind,
                "document_id": source.document_id,
                "locator": source.locator,
            }
            for source in sources
        ],
    )


async def list_messages(
    tx: AsyncSession, ctx: AccessContext, conversation_id: uuid.UUID
) -> list[MessageView]:
    """Every message of the conversation, oldest first, decrypted, with its tables and
    sources."""
    conversation = await _load(tx, ctx, conversation_id, utcnow())
    ref = key_ref(conversation)
    messages = (
        (
            await tx.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.owner_user_id == ctx.user_id,
                )
                .order_by(Message.created_at, Message.id)
            )
        )
        .scalars()
        .all()
    )
    ids = [m.id for m in messages]
    tables: dict[uuid.UUID, list[TableView]] = {}
    sources: dict[uuid.UUID, list[SourceRef]] = {}
    if ids:
        for t in (
            await tx.execute(
                select(MessageTable)
                .where(MessageTable.message_id.in_(ids))
                .order_by(MessageTable.position, MessageTable.id)
            )
        ).scalars():
            tables.setdefault(t.message_id, []).append(
                TableView(
                    id=t.id,
                    position=t.position,
                    title=await _decrypt(tx, ref, table_field(t.id, "title"), t.title_enc),
                    columns=json.loads(
                        await _decrypt(tx, ref, table_field(t.id, "columns"), t.columns_enc) or "[]"
                    ),
                    rows=json.loads(
                        await _decrypt(tx, ref, table_field(t.id, "rows"), t.rows_enc) or "[]"
                    ),
                    source_locators=t.source_locators,
                )
            )
        for s in (
            await tx.execute(
                select(MessageSource)
                .where(MessageSource.message_id.in_(ids))
                .order_by(MessageSource.created_at, MessageSource.id)
            )
        ).scalars():
            sources.setdefault(s.message_id, []).append(SourceRef(s.kind, s.document_id, s.locator))
    return [
        MessageView(
            id=m.id,
            conversation_id=m.conversation_id,
            role=m.role,
            status=m.status,
            content=await _decrypt(tx, ref, message_field(m.id), m.content_enc),
            decline_category=m.decline_category,
            model_name=m.model_name,
            prompt_version=m.prompt_version,
            correlation_id=m.correlation_id,
            latency_ms=m.latency_ms,
            token_usage=m.token_usage,
            created_at=m.created_at,
            updated_at=m.updated_at,
            tables=tuple(tables.get(m.id, ())),
            sources=tuple(sources.get(m.id, ())),
        )
        for m in messages
    ]
