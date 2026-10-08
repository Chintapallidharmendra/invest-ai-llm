"""AC #4 (fields) and AC #5 (streams): format, AAD binding, tamper, truncation,
reordering, appending. Keys come from the cache, so no database is needed."""

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace

import pytest

from app.crypto.cache import get_key_cache
from app.crypto.fields import decrypt_field, decrypt_text, encrypt_field
from app.crypto.files import (
    CHUNK_OVERHEAD,
    CHUNK_SIZE,
    HEADER_BYTES,
    _chunk_aad,
    decrypt_stream,
    encrypt_stream,
    encrypted_size,
)
from app.crypto.kek import IntegrityError
from app.crypto.keys import KeyRef
from tests.crypto.conftest import cached_ref, reset_crypto


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    reset_crypto()
    yield
    reset_crypto()


def _twin(ref: KeyRef, key: bytes, **changes: object) -> KeyRef:
    """Another ref sharing ``key``: any failure is then down to the AAD alone."""
    other = replace(ref, **changes)  # type: ignore[arg-type]
    get_key_cache().put(other.cache_key, key, other.space_id)
    return other


# --- Fields ------------------------------------------------------------------------------


@pytest.mark.parametrize("plaintext", [b"", b"x", "Revenue ₹4,215 cr", os.urandom(10_000)])
async def test_field_round_trip(plaintext: bytes | str) -> None:
    ref, _ = cached_ref()
    blob = await encrypt_field(ref, "title", plaintext)
    data = plaintext.encode() if isinstance(plaintext, str) else plaintext
    assert len(blob) == 1 + 12 + len(data) + 16
    assert blob[0] == 1
    assert await decrypt_field(ref, "title", blob) == data
    if isinstance(plaintext, str):
        assert await decrypt_text(ref, "title", blob) == plaintext


async def test_equal_plaintexts_encrypt_differently() -> None:
    ref, _ = cached_ref()
    first = await encrypt_field(ref, "title", "same")
    second = await encrypt_field(ref, "title", "same")
    assert first != second
    assert first[1:13] != second[1:13]  # fresh nonce


async def test_flipping_any_byte_fails() -> None:
    ref, _ = cached_ref()
    blob = await encrypt_field(ref, "body", b"confidential deal terms")
    for i in range(len(blob)):
        tampered = bytearray(blob)
        tampered[i] ^= 0x01
        with pytest.raises(IntegrityError):
            await decrypt_field(ref, "body", bytes(tampered))
    for broken in (blob[:-1], blob[:20], b"", b"\x02" + blob[1:]):
        with pytest.raises(IntegrityError):
            await decrypt_field(ref, "body", broken)


@pytest.mark.parametrize("moved_to", ["object", "space", "field"])
async def test_ciphertext_moved_to_another_object_space_or_field_fails(moved_to: str) -> None:
    ref, key = cached_ref()
    blob = await encrypt_field(ref, "title", "Project Falcon")
    field = "title"
    target = ref
    if moved_to == "object":
        target = _twin(ref, key, object_id=uuid.uuid4())
    elif moved_to == "space":
        target = _twin(ref, key, space_id=uuid.uuid4())
    else:
        field = "file_name"
    with pytest.raises(IntegrityError):
        await decrypt_field(target, field, blob)


async def test_field_names_are_validated() -> None:
    ref, _ = cached_ref()
    with pytest.raises(ValueError, match="field"):
        await encrypt_field(ref, "Title With Spaces", b"x")


# --- Streams -------------------------------------------------------------------------------


async def _collect(stream: AsyncIterator[bytes]) -> bytes:
    return b"".join([piece async for piece in stream])


def _pieces(data: bytes, size: int) -> list[bytes]:
    return [data[i : i + size] for i in range(0, len(data), size)]


async def _encrypt(ref: KeyRef, data: bytes, **kwargs: object) -> bytes:
    return await _collect(encrypt_stream(ref, _pieces(data, 333_333) or [b""], **kwargs))  # type: ignore[arg-type]


def _split(blob: bytes, chunk_size: int = CHUNK_SIZE) -> tuple[bytes, list[bytes]]:
    sealed = chunk_size + CHUNK_OVERHEAD
    body = blob[HEADER_BYTES:]
    return blob[:HEADER_BYTES], [body[i : i + sealed] for i in range(0, len(body), sealed)]


async def test_a_10mb_file_round_trips_by_streaming() -> None:
    ref, _ = cached_ref()
    data = os.urandom(10 * 1024 * 1024 + 12_345)
    blob = await _encrypt(ref, data)
    assert len(blob) == encrypted_size(len(data))
    _, chunks = _split(blob)
    assert len(chunks) == 11
    assert await _collect(decrypt_stream(ref, _pieces(blob, 65_536))) == data


async def test_decryption_streams_without_reading_the_whole_file() -> None:
    ref, _ = cached_ref()
    data = os.urandom(6 * CHUNK_SIZE)
    blob = await _encrypt(ref, data)
    consumed = 0

    async def source() -> AsyncIterator[bytes]:
        nonlocal consumed
        for piece in _pieces(blob, 64 * 1024):
            consumed += len(piece)
            yield piece

    stream = decrypt_stream(ref, source())
    first = await anext(stream)
    assert first == data[:CHUNK_SIZE]
    assert consumed < 3 * (CHUNK_SIZE + CHUNK_OVERHEAD)  # at most ~2 chunks buffered
    rest = b"".join([piece async for piece in stream])
    assert first + rest == data


@pytest.mark.parametrize("size", [0, 1, CHUNK_SIZE - 1, CHUNK_SIZE, CHUNK_SIZE + 1, 3 * CHUNK_SIZE])
async def test_boundary_sizes_round_trip(size: int) -> None:
    ref, _ = cached_ref()
    data = os.urandom(size)
    blob = await _encrypt(ref, data)
    assert len(blob) == encrypted_size(size)
    assert await _collect(decrypt_stream(ref, [blob])) == data


async def _damaged(ref: KeyRef, damage: str) -> bytes:
    chunk_size = 1024
    data = os.urandom(5 * chunk_size + 100)  # 6 chunks: 0-4 full, 5 short and final
    header, chunks = _split(await _encrypt(ref, data, chunk_size=chunk_size), chunk_size)
    if damage == "drop_last":
        chunks = chunks[:-1]
    elif damage == "drop_first":
        chunks = chunks[1:]
    elif damage == "swap_2_3":
        chunks[2], chunks[3] = chunks[3], chunks[2]
    elif damage == "append_copy":
        chunks.append(chunks[1])
    elif damage == "append_bytes":
        chunks.append(b"\x00" * 50)
    elif damage == "truncate_mid_chunk":
        chunks[-1] = chunks[-1][:30]
    elif damage == "flip_byte":
        chunks[3] = chunks[3][:40] + bytes([chunks[3][40] ^ 1]) + chunks[3][41:]
    elif damage == "header":
        header = header[:-1] + bytes([header[-1] ^ 1])
    elif damage == "header_only":
        chunks = []
    return header + b"".join(chunks)


@pytest.mark.parametrize(
    "damage",
    [
        "drop_last",
        "drop_first",
        "swap_2_3",
        "append_copy",
        "append_bytes",
        "truncate_mid_chunk",
        "flip_byte",
        "header",
        "header_only",
    ],
)
async def test_truncation_reordering_and_appending_are_detected(damage: str) -> None:
    ref, _ = cached_ref()
    blob = await _damaged(ref, damage)
    with pytest.raises(IntegrityError):
        await _collect(decrypt_stream(ref, [blob]))


async def test_a_full_final_chunk_cannot_be_dropped_or_extended() -> None:
    ref, _ = cached_ref()
    blob = await _encrypt(ref, os.urandom(2 * 1024), chunk_size=1024)  # 2 full chunks
    header, chunks = _split(blob, 1024)
    for broken in (header + chunks[0], header + chunks[0] + chunks[1] + chunks[1], header):
        with pytest.raises(IntegrityError):
            await _collect(decrypt_stream(ref, [broken]))
    with pytest.raises(IntegrityError):
        await _collect(decrypt_stream(ref, [b""]))


@pytest.mark.parametrize("moved_to", ["object", "space", "name"])
async def test_a_file_moved_to_another_object_or_stream_fails(moved_to: str) -> None:
    ref, key = cached_ref()
    blob = await _encrypt(ref, b"original document bytes")
    target, name = ref, "content"
    if moved_to == "object":
        target = _twin(ref, key, object_id=uuid.uuid4())
    elif moved_to == "space":
        target = _twin(ref, key, space_id=uuid.uuid4())
    else:
        name = "parquet"
    with pytest.raises(IntegrityError):
        await _collect(decrypt_stream(target, [blob], name=name))


def test_chunk_counter_is_64_bit() -> None:
    ref, _ = cached_ref()
    header = b"IAF1\x01\x00\x10\x00\x00"
    beyond_2gb = 2**31 // CHUNK_SIZE + 5  # chunk index of a > 2 GB stream
    huge = 2**40
    aads = {_chunk_aad(header, ref, "content", i, False) for i in (0, beyond_2gb, huge, huge + 1)}
    assert len(aads) == 4
    assert encrypted_size(3 * 2**30) == HEADER_BYTES + 3 * 2**30 + 3 * 1024 * CHUNK_OVERHEAD
