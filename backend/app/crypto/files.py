"""Streaming file encryption (ADR-028): 1 MiB chunks, each sealed with its own nonce.

Format::

    header:  magic "IAF1" | version (1) | chunk_size (uint32 BE)
    chunk*:  nonce (12) | ciphertext | tag (16)

Every chunk but the last holds exactly ``chunk_size`` plaintext bytes; the last holds
1..chunk_size (0 only for an empty file, which still has its one final chunk). Each
chunk's AAD binds the header, space, object, stream name, chunk index (uint64, so no
wrap-around at any size) and a final-chunk flag, so truncation, reordering, appending
and moving a file to another object are all detected.

Decryption releases each chunk only after it authenticates and never buffers more than
two chunks, so downloads stream. A truncated or extended file fails at the point the
damage is found (for truncation: the end), so consumers must treat an error part-way
through a stream as a failed download.
"""

import os
import struct
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.crypto.kek import NONCE_BYTES, TAG_BYTES, IntegrityError
from app.crypto.keys import Executor, KeyRef, object_key

CHUNK_SIZE: Final = 1024 * 1024
MAGIC: Final = b"IAF1"
FILE_VERSION: Final = 1
_HEADER: Final = struct.Struct(">4sBI")
HEADER_BYTES: Final = _HEADER.size
CHUNK_OVERHEAD: Final = NONCE_BYTES + TAG_BYTES
_FILE_AAD: Final = b"invest-ai/file/v1\x00"
_INDEX: Final = struct.Struct(">QB")

Source = AsyncIterable[bytes] | Iterable[bytes]


def encrypted_size(plaintext_size: int, chunk_size: int = CHUNK_SIZE) -> int:
    chunks = max(1, -(-plaintext_size // chunk_size))
    return HEADER_BYTES + plaintext_size + chunks * CHUNK_OVERHEAD


def _header(chunk_size: int) -> bytes:
    return _HEADER.pack(MAGIC, FILE_VERSION, chunk_size)


def _chunk_aad(header: bytes, key_ref: KeyRef, name: str, index: int, final: bool) -> bytes:
    return (
        _FILE_AAD
        + header
        + key_ref.space_id.bytes
        + key_ref.object_id.bytes
        + name.encode()
        + b"\x00"
        + _INDEX.pack(index, 1 if final else 0)
    )


async def _chunks(source: Source) -> AsyncIterator[bytes]:
    if isinstance(source, AsyncIterable):
        async for piece in source:
            yield piece
    else:
        for piece in source:
            yield piece


def _check_name(name: str) -> None:
    if not name or "\x00" in name or len(name.encode()) > 64:  # noqa: PLR2004
        raise ValueError("stream name must be 1-64 bytes without NUL")


async def encrypt_stream(
    key_ref: KeyRef,
    source: Source,
    *,
    name: str = "content",
    chunk_size: int = CHUNK_SIZE,
    executor: Executor | None = None,
) -> AsyncIterator[bytes]:
    """Yield the encrypted file: the header, then one sealed chunk at a time.

    ``name`` distinguishes several files of one object (e.g. ``original``, ``parquet``).
    """
    _check_name(name)
    aead = AESGCM(await object_key(key_ref, executor))
    header = _header(chunk_size)
    yield header
    buffer = bytearray()
    index = 0

    def seal_chunk(data: bytes, final: bool) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        aad = _chunk_aad(header, key_ref, name, index, final)
        return nonce + aead.encrypt(nonce, data, aad)

    async for piece in _chunks(source):
        buffer += piece
        # Emit only when more data follows, so the last chunk is known to be final.
        while len(buffer) > chunk_size:
            yield seal_chunk(bytes(buffer[:chunk_size]), final=False)
            del buffer[:chunk_size]
            index += 1
    yield seal_chunk(bytes(buffer), final=True)


async def decrypt_stream(
    key_ref: KeyRef,
    source: Source,
    *,
    name: str = "content",
    executor: Executor | None = None,
) -> AsyncIterator[bytes]:
    """Yield the plaintext chunk by chunk; raises :class:`IntegrityError` on any damage."""
    _check_name(name)
    aead = AESGCM(await object_key(key_ref, executor))
    buffer = bytearray()
    header: bytes | None = None
    sealed_size = 0
    index = 0

    def open_chunk(data: bytes, final: bool) -> bytes:
        assert header is not None  # noqa: S101
        aad = _chunk_aad(header, key_ref, name, index, final)
        try:
            return aead.decrypt(data[:NONCE_BYTES], data[NONCE_BYTES:], aad)
        except InvalidTag:
            raise IntegrityError("file chunk failed authentication") from None

    async for piece in _chunks(source):
        buffer += piece
        if header is None:
            if len(buffer) < HEADER_BYTES:
                continue
            header = bytes(buffer[:HEADER_BYTES])
            magic, version, chunk_size = _HEADER.unpack(header)
            if magic != MAGIC or version != FILE_VERSION or not 0 < chunk_size <= 64 * CHUNK_SIZE:
                raise IntegrityError("not an encrypted file of a known format")
            sealed_size = chunk_size + CHUNK_OVERHEAD
            del buffer[:HEADER_BYTES]
        while len(buffer) > sealed_size:  # more follows: this chunk is not the last
            yield open_chunk(bytes(buffer[:sealed_size]), final=False)
            del buffer[:sealed_size]
            index += 1

    if header is None or len(buffer) < CHUNK_OVERHEAD:
        raise IntegrityError("encrypted file is truncated")
    yield open_chunk(bytes(buffer), final=True)
