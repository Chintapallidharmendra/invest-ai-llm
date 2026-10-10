"""Encrypted output files on disk (ADR-028).

Files are written atomically: the ciphertext goes to a temporary file in the target
directory, is flushed to disk, then renamed into place, so a reader never sees a partial
file and a crash leaves at most a ``.tmp-*`` file behind. Only ciphertext is ever
written; plaintext never touches disk.
"""

import asyncio
import os
import uuid
from collections.abc import AsyncIterable, AsyncIterator
from pathlib import Path
from typing import Final

from app.crypto.files import CHUNK_OVERHEAD, CHUNK_SIZE
from app.outputs.settings import get_outputs_settings

READ_SIZE: Final = CHUNK_SIZE + CHUNK_OVERHEAD


def relative_path(space_id: uuid.UUID, output_id: uuid.UUID) -> str:
    return f"{space_id}/{output_id}"


def absolute_path(relative: str) -> Path:
    base = get_outputs_settings().files_dir.resolve()
    path = (base / relative).resolve()
    if path.parent.parent != base:
        raise ValueError("output path escapes the files directory")
    return path


async def write_atomic(relative: str, chunks: AsyncIterable[bytes]) -> int:
    """Write ``chunks`` to ``relative``; returns the bytes written."""
    path = absolute_path(relative)
    await asyncio.to_thread(path.parent.mkdir, mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    written = 0
    fh = await asyncio.to_thread(open, tmp, "xb")
    try:
        async for chunk in chunks:
            await asyncio.to_thread(fh.write, chunk)
            written += len(chunk)
        await asyncio.to_thread(fh.flush)
        await asyncio.to_thread(os.fsync, fh.fileno())
    except BaseException:
        fh.close()
        tmp.unlink(missing_ok=True)
        raise
    fh.close()
    await asyncio.to_thread(os.replace, tmp, path)
    return written


async def read_chunks(relative: str) -> AsyncIterator[bytes]:
    """The stored ciphertext, one sealed chunk's worth at a time."""
    fh = await asyncio.to_thread(open, absolute_path(relative), "rb")
    try:
        while chunk := await asyncio.to_thread(fh.read, READ_SIZE):
            yield chunk
    finally:
        fh.close()


async def remove(relative: str) -> bool:
    """Delete the file; ``False`` if it was already gone."""
    try:
        await asyncio.to_thread(absolute_path(relative).unlink)
    except FileNotFoundError:
        return False
    return True
