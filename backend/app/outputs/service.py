"""Outputs: generated files stored encrypted, listed, downloaded with a watermark and
deleted (FR-024, ADR-023, ADR-028).

:func:`create_output` is what generating features call (9.11, 9.13, 10.6), inside their
own transaction (opened with ``ctx.rls_settings()``). The other functions open their
own transactions.

**Encryption.** Each output has an object key ``("output", id)``. The file is streamed
through ``crypto.files.encrypt_stream`` straight to disk, so plaintext never touches
disk; the title and operations log are encrypted fields (``title``, ``ops_log``).

**Downloads.** The token check, the use count and the ``outputs.downloaded`` audit row
are committed *before* any byte is sent, so every download attempt is on record. The
file is then decrypted chunk by chunk and watermarked for the downloading user. An
error part-way through raises out of the stream, which aborts the HTTP response: a
partial file is never presented as complete.
"""

import asyncio
import hashlib
import json
import re
import unicodedata
import uuid
from collections.abc import AsyncIterable, AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer
from app.audit.types import KeyedHash
from app.auth.service import correlation_id
from app.core.db import transaction
from app.core.ids import new_uuid7
from app.core.timeutil import utcnow
from app.crypto.fields import decrypt_text, encrypt_field
from app.crypto.files import decrypt_stream, encrypt_stream
from app.crypto.keys import KeyRef, create_object_key
from app.crypto.shred import shred_object
from app.outputs import storage, tokens, watermark
from app.outputs.audit_events import (
    DownloadLinkIssued,
    OutputCreated,
    OutputDeleted,
    OutputDownloaded,
)
from app.outputs.models import Output, OutputKind, OutputOrigin
from app.outputs.settings import get_outputs_settings
from app.spaces.access import AccessContext
from app.spaces.models import Space, SpaceKind
from app.spaces.service import CODE_NAME_FIELD, code_name_ref

OBJECT_TYPE: Final = "output"
FILE_STREAM: Final = "content"
TITLE_MAX_LENGTH: Final = 200
MEDIA_TYPES: Final = {
    OutputKind.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    OutputKind.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    OutputKind.CSV: "text/csv; charset=utf-8",
}


class OutputNotFoundError(Exception):
    """Not the caller's, not in one of their open spaces, deleted or expired."""


class SpaceNotAccessibleError(Exception):
    pass


class OutputTooLargeError(Exception):
    """An XLSX/DOCX too big to watermark in memory."""


@dataclass(frozen=True, slots=True)
class OutputView:
    id: uuid.UUID
    space_id: uuid.UUID
    kind: OutputKind
    origin: OutputOrigin
    title: str
    size_bytes: int
    source_document_ids: tuple[uuid.UUID, ...]
    version_of_id: uuid.UUID | None
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class OutputDetail(OutputView):
    versions: tuple[OutputView, ...]  # outputs made as new versions of this one


@dataclass(frozen=True, slots=True)
class Download:
    filename: str
    media_type: str
    chunks: AsyncIterator[bytes]


def key_ref(output: Output) -> KeyRef:
    return KeyRef(output.space_id, OBJECT_TYPE, output.id)


def clean_title(title: str) -> str:
    cleaned = " ".join(title.split())
    if not 1 <= len(cleaned) <= TITLE_MAX_LENGTH:
        raise ValueError("title must be 1-200 characters")
    return cleaned


async def _view(tx: AsyncSession, output: Output) -> OutputView:
    return OutputView(
        id=output.id,
        space_id=output.space_id,
        kind=output.kind,
        origin=output.origin,
        title=await decrypt_text(key_ref(output), "title", output.title_enc, executor=tx),
        size_bytes=output.size_bytes,
        source_document_ids=tuple(output.source_document_ids),
        version_of_id=output.version_of_id,
        created_at=output.created_at,
        expires_at=output.expires_at,
    )


def _visible(ctx: AccessContext, now: datetime) -> list[Any]:
    return [
        Output.owner_user_id == ctx.user_id,
        Output.deleted_at.is_(None),
        Output.expires_at > now,
    ]


async def _load(
    tx: AsyncSession, ctx: AccessContext, output_id: uuid.UUID, now: datetime, *, lock: bool = False
) -> Output:
    stmt = select(Output).where(Output.id == output_id, *_visible(ctx, now))
    if lock:
        stmt = stmt.with_for_update()
    output = (await tx.execute(stmt)).scalar_one_or_none()
    if output is None or not ctx.can_access(output.space_id):
        raise OutputNotFoundError
    return output


# --- Create -------------------------------------------------------------------------------


class _Measured:
    """Passes chunks through while counting bytes and hashing them."""

    def __init__(self, source: AsyncIterable[bytes]) -> None:
        self.source = source
        self.size = 0
        self.sha256 = hashlib.sha256()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self.source:
            self.size += len(chunk)
            self.sha256.update(chunk)
            yield chunk


async def create_output(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    space_id: uuid.UUID,
    kind: OutputKind,
    origin: OutputOrigin,
    content: AsyncIterable[bytes],
    title: str,
    source_document_ids: Sequence[uuid.UUID] = (),
    version_of_id: uuid.UUID | None = None,
    ops_log: Sequence[Any] | None = None,
    now: datetime | None = None,
) -> OutputView:
    """Encrypt ``content`` to disk and record the output, expiring after the space's
    retention period. ``session`` must run under ``ctx.rls_settings()``."""
    now = now or utcnow()
    title = clean_title(title)
    if not ctx.can_access(space_id):
        raise SpaceNotAccessibleError
    retention = (
        await session.execute(
            select(Space.retention_days).where(Space.id == space_id, Space.closed_at.is_(None))
        )
    ).scalar_one_or_none()
    if retention is None:
        raise SpaceNotAccessibleError
    if version_of_id is not None:
        previous = await _load(session, ctx, version_of_id, now)
        if previous.space_id != space_id:
            raise OutputNotFoundError

    output_id = new_uuid7()
    ref = await create_object_key(session, space_id, OBJECT_TYPE, output_id)
    relative = storage.relative_path(space_id, output_id)
    measured = _Measured(content)
    await storage.write_atomic(
        relative, encrypt_stream(ref, measured, name=FILE_STREAM, executor=session)
    )
    try:
        title_enc = await encrypt_field(ref, "title", title, executor=session)
        ops_log_enc = (
            None
            if ops_log is None
            else await encrypt_field(ref, "ops_log", json.dumps(list(ops_log)), executor=session)
        )
        expires_at = now + timedelta(days=retention)
        await session.execute(
            insert(Output).values(
                id=output_id,
                space_id=space_id,
                owner_user_id=ctx.user_id,
                kind=kind,
                origin=origin,
                source_document_ids=list(dict.fromkeys(source_document_ids)),
                version_of_id=version_of_id,
                title_enc=title_enc,
                file_path=relative,
                size_bytes=measured.size,
                content_hash=KeyedHash.of(measured.sha256.digest()),
                ops_log_enc=ops_log_enc,
                expires_at=expires_at,
                created_at=now,
                updated_at=now,
            )
        )
        await writer.record(
            OutputCreated(
                output_id=output_id,
                space_id=space_id,
                kind=kind,
                origin=origin,
                size_bytes=measured.size,
            ),
            actor_user_id=ctx.user_id,
            correlation_id=correlation_id(),
            target_type=OBJECT_TYPE,
            target_id=output_id,
            session=session,
        )
    except BaseException:
        await storage.remove(relative)
        raise
    return OutputView(
        id=output_id,
        space_id=space_id,
        kind=kind,
        origin=origin,
        title=title,
        size_bytes=measured.size,
        source_document_ids=tuple(dict.fromkeys(source_document_ids)),
        version_of_id=version_of_id,
        created_at=now,
        expires_at=expires_at,
    )


# --- Read ---------------------------------------------------------------------------------


async def list_outputs(
    ctx: AccessContext, *, space_id: uuid.UUID | None = None
) -> list[OutputView]:
    """The caller's outputs in their open spaces (or one of them), newest first."""
    now = utcnow()
    spaces = ctx.space_ids if space_id is None else ctx.space_ids & {space_id}
    async with transaction(context=ctx.rls_settings()) as tx:
        rows = (
            await tx.execute(
                select(Output)
                .where(*_visible(ctx, now), Output.space_id.in_(spaces))
                .order_by(Output.created_at.desc(), Output.id.desc())
            )
        ).scalars()
        return [await _view(tx, output) for output in rows]


async def get_output(ctx: AccessContext, output_id: uuid.UUID) -> OutputDetail:
    now = utcnow()
    async with transaction(context=ctx.rls_settings()) as tx:
        output = await _load(tx, ctx, output_id, now)
        versions = (
            await tx.execute(
                select(Output)
                .where(Output.version_of_id == output_id, *_visible(ctx, now))
                .order_by(Output.created_at, Output.id)
            )
        ).scalars()
        view = await _view(tx, output)
        return OutputDetail(
            **{f: getattr(view, f) for f in OutputView.__dataclass_fields__},
            versions=tuple([await _view(tx, v) for v in versions]),
        )


# --- Links and downloads ------------------------------------------------------------------


async def issue_download_link(
    ctx: AccessContext, output_id: uuid.UUID, *, now: datetime | None = None
) -> tokens.IssuedToken:
    now = now or utcnow()
    async with transaction(context=ctx.rls_settings()) as tx:
        output = await _load(tx, ctx, output_id, now)
        issued = await tokens.issue(tx, ctx, output, now=now)
        await writer.record(
            DownloadLinkIssued(output_id=output_id, expires_at=issued.expires_at),
            actor_user_id=ctx.user_id,
            correlation_id=correlation_id(),
            target_type=OBJECT_TYPE,
            target_id=output_id,
            session=tx,
        )
    return issued


_UNSAFE_FILENAME: Final = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]+')


def download_filename(title: str, kind: OutputKind) -> str:
    """The title as a file name: no path or control characters, at most 120 characters."""
    name = _UNSAFE_FILENAME.sub("_", unicodedata.normalize("NFC", title)).strip(" ._") or "output"
    return f"{name[:120]}.{kind.value}"


async def open_download(
    ctx: AccessContext, token: str, *, username: str, now: datetime | None = None
) -> Download:
    """Check the token for the caller, record the download, and return the watermarked
    stream. ``OutputNotFoundError`` for an unknown, expired or someone else's token, or
    an output the caller can no longer see."""
    now = now or utcnow()
    async with transaction(context=ctx.rls_settings()) as tx:
        output = await tokens.redeem(tx, ctx, token, now=now)
        if output is None:
            raise OutputNotFoundError
        if (
            output.kind is not OutputKind.CSV
            and output.size_bytes > get_outputs_settings().max_watermark_bytes
        ):
            raise OutputTooLargeError
        title = await decrypt_text(key_ref(output), "title", output.title_enc, executor=tx)
        code_name = await _code_name(tx, output.space_id)
        await writer.record(
            OutputDownloaded(
                output_id=output.id,
                kind=output.kind,
                size_bytes=output.size_bytes,
                file_digest=KeyedHash(output.content_hash),
            ),
            actor_user_id=ctx.user_id,
            correlation_id=correlation_id(),
            target_type=OBJECT_TYPE,
            target_id=output.id,
            session=tx,
        )
    mark = watermark.Watermark(username=username, at=now, code_name=code_name)
    return Download(
        filename=download_filename(title, output.kind),
        media_type=MEDIA_TYPES[output.kind],
        chunks=_watermarked(output, mark),
    )


async def _code_name(tx: AsyncSession, space_id: uuid.UUID) -> str | None:
    kind, encrypted = (
        await tx.execute(select(Space.kind, Space.code_name_enc).where(Space.id == space_id))
    ).one()
    if kind is SpaceKind.PRIVATE or encrypted is None:
        return None
    return await decrypt_text(code_name_ref(space_id), CODE_NAME_FIELD, encrypted, executor=tx)


async def _watermarked(output: Output, mark: watermark.Watermark) -> AsyncIterator[bytes]:
    plaintext = decrypt_stream(
        key_ref(output), storage.read_chunks(output.file_path), name=FILE_STREAM
    )
    if output.kind is OutputKind.CSV:
        async for chunk in watermark.watermark_csv(plaintext, mark):
            yield chunk
        return
    data = bytearray()
    async for chunk in plaintext:
        data += chunk
    stamp = watermark.watermark_xlsx if output.kind is OutputKind.XLSX else watermark.watermark_docx
    yield await asyncio.to_thread(stamp, bytes(data), mark)


# --- Delete -------------------------------------------------------------------------------


async def delete_output(ctx: AccessContext, output_id: uuid.UUID) -> None:
    """Permanent (FR-053): shred the key, delete the row (and its links), remove the file."""
    async with transaction(context=ctx.rls_settings()) as tx:
        output = await _load(tx, ctx, output_id, utcnow(), lock=True)
        await shred_object(tx, OBJECT_TYPE, output_id)
        await tx.execute(delete(Output).where(Output.id == output_id))
        await writer.record(
            OutputDeleted(output_id=output_id, space_id=output.space_id),
            actor_user_id=ctx.user_id,
            correlation_id=correlation_id(),
            target_type=OBJECT_TYPE,
            target_id=output_id,
            session=tx,
        )
    # After the commit: the key is gone, so the file is already unreadable.
    await storage.remove(output.file_path)
