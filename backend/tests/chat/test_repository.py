"""AC #1 (owner-only RLS), AC #2 (encryption), AC #5 (repository for 3.2)."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.auth.models import User
from app.chat import repository
from app.chat.models import MessageRole, MessageStatus
from app.chat.repository import SourceRef
from app.core.db import transaction
from app.core.timeutil import utcnow
from app.crypto.fields import IntegrityError, decrypt_field
from app.crypto.keys import KeyDestroyed, KeyRef
from tests.chat.conftest import context_of
from tests.conftest import PgDatabase
from tests.spaces.conftest import as_migrator, as_user, make_user, make_workspace

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("spaces_db")]

TABLES = (
    "conversations",
    "conversation_documents",
    "messages",
    "message_tables",
    "message_sources",
)
PLANTED_TITLE = "Planted Title Falcon 7731"
PLANTED_TEXT = "Planted message about EBITDA 4410"
PLANTED_CELL = "Planted cell 99812"


async def seeded_conversation(user: User, space_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """A conversation with a title, two messages, a table and sources; returns
    (conversation_id, assistant_message_id)."""
    ctx = await context_of(user)
    async with transaction(context=ctx.rls_settings()) as tx:
        conversation = await repository.create_conversation(tx, ctx, space_id)
        await repository.rename_conversation(tx, ctx, conversation.id, PLANTED_TITLE)
        await repository.append_message(
            tx, ctx, conversation.id, role=MessageRole.USER, content="What is EBITDA?"
        )
        answer = await repository.append_message(
            tx,
            ctx,
            conversation.id,
            role=MessageRole.ASSISTANT,
            content=PLANTED_TEXT,
            model_name="qwen3-32b",
            prompt_version="chat-v1",
            latency_ms=1200,
            token_usage={"prompt": 100, "completion": 20},
        )
        await repository.add_message_table(
            tx,
            ctx,
            answer.id,
            title="Margins",
            columns=["Year", "EBITDA"],
            rows=[["FY24", PLANTED_CELL], ["FY25", 12.5]],
            source_locators=[{"document_id": None, "locator": "p.12"}],
        )
        await repository.add_message_sources(
            tx, ctx, answer.id, [SourceRef("document", uuid.uuid4(), "p.12")]
        )
        # No document resolver exists yet (9.1), so select a document directly.
        await tx.execute(
            text(
                "INSERT INTO conversation_documents"
                " (conversation_id, document_id, space_id, owner_user_id)"
                " VALUES (:c, :d, :s, :o)"
            ),
            {"c": conversation.id, "d": uuid.uuid4(), "s": space_id, "o": user.id},
        )
    return conversation.id, answer.id


async def test_round_trip_messages_tables_sources() -> None:
    owner = await make_user("anika")
    ctx = await context_of(owner)
    private = next(iter(ctx.space_ids))
    conversation_id, answer_id = await seeded_conversation(owner, private)

    async with transaction(context=ctx.rls_settings()) as tx:
        detail = await repository.get_conversation(tx, ctx, conversation_id)
        messages = await repository.list_messages(tx, ctx, conversation_id)
    assert detail.title == PLANTED_TITLE
    assert [(m.role, m.content) for m in messages] == [
        (MessageRole.USER, "What is EBITDA?"),
        (MessageRole.ASSISTANT, PLANTED_TEXT),
    ]
    answer = messages[1]
    assert answer.id == answer_id
    assert (answer.model_name, answer.prompt_version, answer.latency_ms) == (
        "qwen3-32b",
        "chat-v1",
        1200,
    )
    assert answer.token_usage == {"prompt": 100, "completion": 20}
    (table,) = answer.tables
    assert (table.title, table.columns, table.rows) == (
        "Margins",
        ["Year", "EBITDA"],
        [["FY24", PLANTED_CELL], ["FY25", 12.5]],
    )
    assert table.source_locators == [{"document_id": None, "locator": "p.12"}]
    (source,) = answer.sources
    assert (source.kind, source.locator) == ("document", "p.12")


async def test_update_message_and_summary() -> None:
    owner = await make_user("bo")
    ctx = await context_of(owner)
    private = next(iter(ctx.space_ids))
    async with transaction(context=ctx.rls_settings()) as tx:
        conversation = await repository.create_conversation(tx, ctx, private)
        pending = await repository.append_message(
            tx, ctx, conversation.id, role=MessageRole.ASSISTANT, status=MessageStatus.PENDING
        )
        await repository.update_message(
            tx, ctx, pending.id, status=MessageStatus.STREAMING, content="Partial"
        )
        await repository.update_message(
            tx, ctx, pending.id, status=MessageStatus.COMPLETE, content="Full answer", latency_ms=5
        )
        await repository.set_summary(tx, ctx, conversation.id, "Summary so far", pending.id)
        (message,) = await repository.list_messages(tx, ctx, conversation.id)
        detail = await repository.get_conversation(tx, ctx, conversation.id)
    assert (message.status, message.content, message.latency_ms) == (
        MessageStatus.COMPLETE,
        "Full answer",
        5,
    )
    assert (detail.summary, detail.summary_upto_message_id) == ("Summary so far", pending.id)

    async with transaction(context=ctx.rls_settings()) as tx:
        await repository.update_message(
            tx, ctx, pending.id, status=MessageStatus.DECLINED, decline_category="out_of_scope"
        )
        (message,) = await repository.list_messages(tx, ctx, conversation.id)
    assert message.content == "Full answer"  # omitted fields are kept
    assert message.decline_category == "out_of_scope"


async def test_workspace_colleague_sees_nothing(pg: PgDatabase) -> None:
    a = await make_user("alice")
    b = await make_user("bilal")
    workspace = await make_workspace("Falcon", a, b)
    conversation_id, _ = await seeded_conversation(a, workspace)

    # Raw SQL under B's RLS context: no rows in any table. A sees their own.
    for table in TABLES:
        assert await as_user(b.id, f"SELECT 1 FROM {table}") == []  # noqa: S608
        assert await as_user(None, f"SELECT 1 FROM {table}") == []  # noqa: S608
        assert await as_user(a.id, f"SELECT 1 FROM {table}"), table  # noqa: S608
    # And as the owner (app_migrator), FORCE keeps it at 0 too.
    for table in TABLES:
        rows = await as_migrator(pg.migrator_url, f"SELECT count(*) FROM {table}")  # noqa: S608
        assert rows == [(0,)], table

    ctx_b = await context_of(b)
    async with transaction(context=ctx_b.rls_settings()) as tx:
        with pytest.raises(repository.ConversationNotFoundError):
            await repository.get_conversation(tx, ctx_b, conversation_id)
        page = await repository.list_conversations(tx, ctx_b)
    assert page.items == []


async def test_b_cannot_write_into_as_conversation() -> None:
    a = await make_user("alice")
    b = await make_user("bilal")
    workspace = await make_workspace("Falcon", a, b)
    conversation_id, _ = await seeded_conversation(a, workspace)
    async with transaction(context={"app.user_id": str(b.id)}) as tx:
        updated = await tx.execute(
            text("UPDATE conversations SET title_enc = NULL WHERE id = :id"),
            {"id": conversation_id},
        )
        assert getattr(updated, "rowcount", None) == 0
    # Forging a message row with A as owner fails WITH CHECK.
    with pytest.raises(Exception, match="row-level security"):
        async with transaction(context={"app.user_id": str(b.id)}) as tx:
            await tx.execute(
                text(
                    "INSERT INTO messages (conversation_id, space_id, owner_user_id, role, status)"
                    " VALUES (:c, :s, :o, 'user', 'complete')"
                ),
                {"c": conversation_id, "s": workspace, "o": a.id},
            )


async def test_children_cannot_diverge_from_their_conversation() -> None:
    a = await make_user("alice")
    ctx = await context_of(a)
    private = next(iter(ctx.space_ids))
    workspace = await make_workspace("Falcon", a)
    conversation_id, _ = await seeded_conversation(a, private)
    with pytest.raises(Exception, match="foreign key"):
        async with transaction(context=ctx.rls_settings()) as tx:
            await tx.execute(
                text(
                    "INSERT INTO messages (conversation_id, space_id, owner_user_id, role, status)"
                    " VALUES (:c, :s, :o, 'user', 'complete')"
                ),
                {"c": conversation_id, "s": workspace, "o": a.id},
            )


async def test_no_plaintext_content_in_the_database() -> None:
    a = await make_user("alice")
    ctx = await context_of(a)
    await seeded_conversation(a, next(iter(ctx.space_ids)))
    planted = (PLANTED_TITLE, PLANTED_TEXT, PLANTED_CELL, "Margins", "EBITDA")
    for table in TABLES:
        # Every row its owner can see: each value, bytea included, as raw bytes.
        rows = await as_user(a.id, f"SELECT * FROM {table}")  # noqa: S608
        assert rows, table
        values = [v if isinstance(v, bytes) else str(v).encode() for row in rows for v in row]
        for marker in planted:
            assert not any(marker.encode() in v for v in values), (table, marker)


async def test_delete_shreds_the_key_and_rows(pg: PgDatabase) -> None:
    a = await make_user("alice")
    ctx = await context_of(a)
    conversation_id, answer_id = await seeded_conversation(a, next(iter(ctx.space_ids)))
    async with transaction(context=ctx.rls_settings()) as tx:
        old = (
            await tx.execute(
                text("SELECT space_id, content_enc FROM messages WHERE id = :id"), {"id": answer_id}
            )
        ).one()
        await repository.delete_conversation(tx, ctx, conversation_id)
    ref = KeyRef(old.space_id, repository.OBJECT_TYPE, conversation_id)
    with pytest.raises(KeyDestroyed):
        await decrypt_field(ref, repository.message_field(answer_id), old.content_enc)
    for table in TABLES:
        assert await as_user(a.id, f"SELECT 1 FROM {table}") == []  # noqa: S608
    keys = await as_migrator(
        pg.migrator_url,
        "SELECT count(*) FROM object_keys WHERE object_id = :id",
        {"id": conversation_id},
    )
    assert keys == [(0,)]


async def test_ciphertext_moved_between_messages_fails() -> None:
    a = await make_user("alice")
    ctx = await context_of(a)
    conversation_id, answer_id = await seeded_conversation(a, next(iter(ctx.space_ids)))
    async with transaction(context=ctx.rls_settings()) as tx:
        await tx.execute(
            text(
                "UPDATE messages SET content_enc = (SELECT content_enc FROM messages WHERE id = :a)"
                " WHERE id <> :a"
            ),
            {"a": answer_id},
        )
    with pytest.raises(IntegrityError):
        async with transaction(context=ctx.rls_settings()) as tx:
            await repository.list_messages(tx, ctx, conversation_id)


async def test_owner_removed_from_workspace_loses_access() -> None:
    a = await make_user("alice")
    lead = await make_user("lead")
    workspace = await make_workspace("Falcon", lead, a)
    conversation_id, _ = await seeded_conversation(a, workspace)
    async with transaction(context={"app.user_id": str(lead.id)}) as tx:
        await tx.execute(
            text("DELETE FROM space_members WHERE space_id = :s AND user_id = :u"),
            {"s": workspace, "u": a.id},
        )
    ctx = await context_of(a)
    async with transaction(context=ctx.rls_settings()) as tx:
        with pytest.raises(repository.ConversationNotFoundError):
            await repository.get_conversation(tx, ctx, conversation_id)
        assert (await repository.list_conversations(tx, ctx)).items == []
    assert await as_user(a.id, "SELECT 1 FROM conversations") == []


async def test_expired_conversations_are_hidden() -> None:
    a = await make_user("alice")
    ctx = await context_of(a)
    private = next(iter(ctx.space_ids))
    async with transaction(context=ctx.rls_settings()) as tx:
        created = await repository.create_conversation(
            tx, ctx, private, now=utcnow() - timedelta(days=31)
        )
    assert created.expires_at < utcnow()
    async with transaction(context=ctx.rls_settings()) as tx:
        with pytest.raises(repository.ConversationNotFoundError):
            await repository.get_conversation(tx, ctx, created.id)


def test_field_names_are_valid_and_distinct() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    names = {
        repository.message_field(a),
        repository.message_field(b),
        repository.table_field(a, "rows"),
        repository.table_field(a, "columns"),
    }
    assert len(names) == 4
    assert all(len(n) <= 63 for n in names)
