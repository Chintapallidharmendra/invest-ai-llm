"""AC #2 (tables, grants, migration), #3 (wrapping), #6 (shredding), #7 (rotation)."""

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError as DbIntegrityError
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.db import transaction
from app.crypto import rotate
from app.crypto.cache import get_key_cache
from app.crypto.fields import decrypt_field, encrypt_field
from app.crypto.files import decrypt_stream, encrypt_stream
from app.crypto.kek import CryptoError, IntegrityError, Kek, KekError, get_kek
from app.crypto.keys import (
    KeyDestroyed,
    KeyRef,
    create_object_key,
    create_space_key,
    object_key,
    space_key,
    space_key_aad,
)
from app.crypto.models import ObjectKey, SpaceKey
from app.crypto.shred import shred_object, shred_space
from tests.conftest import BACKEND_DIR, PgDatabase
from tests.crypto.conftest import new_object, new_space, reset_crypto

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("crypto_db")]


async def _as_migrator(pg: PgDatabase, sql: str, params: dict[str, object] | None = None) -> None:
    engine = create_async_engine(pg.migrator_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params or {})
    finally:
        await engine.dispose()


def _cold() -> None:
    get_key_cache().clear()


# --- AC #2: tables -------------------------------------------------------------------------


async def test_rows_hold_wrapped_keys_only() -> None:
    ref = await new_object()
    key = await object_key(ref)
    skey = await space_key(ref.space_id)
    async with transaction() as tx:
        space_row = (await tx.execute(select(SpaceKey))).scalar_one()
        object_row = (await tx.execute(select(ObjectKey))).scalar_one()
    assert (space_row.space_id, space_row.kek_version) == (ref.space_id, 1)
    assert (object_row.space_id, object_row.object_type) == (ref.space_id, "document")
    for row in (space_row, object_row):
        assert len(row.wrapped_key) == 61
    assert key not in object_row.wrapped_key
    assert skey not in space_row.wrapped_key


async def test_app_rw_may_not_update_key_rows() -> None:
    await new_object()
    for table in ("space_keys", "object_keys"):
        with pytest.raises(ProgrammingError, match="permission denied"):
            async with transaction() as tx:
                await tx.execute(text(f"UPDATE {table} SET created_at = now()"))  # noqa: S608


async def test_a_duplicate_object_key_is_rejected() -> None:
    ref = await new_object()
    async with transaction() as tx:
        row = (await tx.execute(select(ObjectKey))).scalar_one()
    with pytest.raises(DbIntegrityError, match="pk_object_keys"):
        async with transaction() as tx:
            await tx.execute(
                text(
                    "INSERT INTO object_keys (object_type, object_id, space_id, wrapped_key) "
                    "VALUES (:t, :o, :s, :w)"
                ),
                {"t": ref.object_type, "o": ref.object_id, "s": ref.space_id, "w": row.wrapped_key},
            )


def test_migration_down_and_up(pg: PgDatabase) -> None:
    env = {**os.environ, "APP_MIGRATOR_DATABASE_URL": pg.migrator_url}
    for args in (["downgrade", "7_1_audit"], ["upgrade", "head"]):
        result = subprocess.run(  # noqa: S603 (fixed argv)
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr[-2000:]


# --- AC #3: wrapping and AAD -----------------------------------------------------------------


async def test_a_space_key_unwrapped_with_another_space_id_fails(pg: PgDatabase) -> None:
    ref = await new_object()
    other_space = await new_space()
    async with transaction() as tx:
        row = (await tx.execute(select(SpaceKey))).scalar_one()
        # Copy space A's wrapped key into space B's row.
        await tx.execute(
            text("INSERT INTO space_keys (space_id, wrapped_key, kek_version) VALUES (:s, :w, 1)"),
            {"s": other_space, "w": row.wrapped_key},
        )
    _cold()
    with pytest.raises(IntegrityError):
        await space_key(other_space)
    assert await space_key(ref.space_id)


async def test_an_object_key_moved_to_another_object_fails() -> None:
    ref = await new_object()
    moved = KeyRef(ref.space_id, "document", uuid.uuid4())
    async with transaction() as tx:
        row = (await tx.execute(select(ObjectKey))).scalar_one()
        await tx.execute(
            text(
                "INSERT INTO object_keys (object_type, object_id, space_id, wrapped_key) "
                "VALUES ('document', :o, :s, :w)"
            ),
            {"o": moved.object_id, "s": ref.space_id, "w": row.wrapped_key},
        )
    _cold()
    with pytest.raises(IntegrityError):
        await object_key(moved)


async def test_create_is_idempotent_and_concurrency_safe() -> None:
    space_id, object_id = await new_space(), uuid.uuid4()
    async with transaction() as tx:
        assert await create_space_key(tx, space_id)
    async with transaction() as tx:
        assert not await create_space_key(tx, space_id)

    async def create() -> bytes:
        async with transaction() as tx:
            ref = await create_object_key(tx, space_id, "document", object_id)
        return await object_key(ref)

    _cold()
    keys = await asyncio.gather(*(create() for _ in range(10)))
    _cold()
    stored = await object_key(KeyRef(space_id, "document", object_id))
    assert set(keys) == {stored}
    async with transaction() as tx:
        assert len((await tx.execute(select(ObjectKey))).scalars().all()) == 1


async def test_an_object_cannot_be_rekeyed_into_another_space() -> None:
    ref = await new_object()
    other_space = await new_space()
    async with transaction() as tx:
        await create_space_key(tx, other_space)
    _cold()
    with pytest.raises(CryptoError, match="another space"):
        async with transaction() as tx:
            await create_object_key(tx, other_space, ref.object_type, ref.object_id)
    with pytest.raises(CryptoError, match="another space"):
        await object_key(KeyRef(other_space, ref.object_type, ref.object_id))


async def test_encrypt_works_inside_the_creating_transaction() -> None:
    space_id = await new_space()
    async with transaction() as tx:
        await create_space_key(tx, space_id)
        ref = await create_object_key(tx, space_id, "conversation", uuid.uuid4())
        blob = await encrypt_field(ref, "title", "Q2 review")
    _cold()  # after commit, a cold cache reads the key rows
    assert await decrypt_field(ref, "title", blob) == b"Q2 review"


async def test_unknown_keys_are_destroyed_keys() -> None:
    with pytest.raises(KeyDestroyed):
        await object_key(KeyRef(uuid.uuid4(), "document", uuid.uuid4()))
    with pytest.raises(KeyDestroyed):
        await space_key(uuid.uuid4())


# --- AC #6: shredding ----------------------------------------------------------------------


async def _collect_stream(ref: KeyRef, blob: bytes) -> bytes:
    return b"".join([p async for p in decrypt_stream(ref, [blob])])


async def test_shred_object_destroys_it_even_with_a_warm_cache() -> None:
    ref = await new_object()
    keep = await new_object(ref.space_id)
    blob = await encrypt_field(ref, "body", b"secret")
    file_blob = b"".join([p async for p in encrypt_stream(ref, [b"file bytes"])])
    kept = await encrypt_field(keep, "body", b"other")
    assert await decrypt_field(ref, "body", blob) == b"secret"  # cache now warm
    async with transaction() as tx:
        assert await shred_object(tx, ref.object_type, ref.object_id)
        assert not await shred_object(tx, ref.object_type, ref.object_id)
    with pytest.raises(KeyDestroyed):
        await decrypt_field(ref, "body", blob)
    with pytest.raises(KeyDestroyed):
        await _collect_stream(ref, file_blob)
    assert await decrypt_field(keep, "body", kept) == b"other"


async def test_shred_space_destroys_every_object_in_it() -> None:
    first = await new_object()
    refs = [first, *[await new_object(first.space_id) for _ in range(3)]]
    elsewhere = await new_object()
    blobs = [await encrypt_field(r, "body", b"x") for r in refs]
    other_blob = await encrypt_field(elsewhere, "body", b"y")
    async with transaction() as tx:
        assert await shred_space(tx, first.space_id) == 4
    for ref, blob in zip(refs, blobs, strict=True):
        with pytest.raises(KeyDestroyed):
            await decrypt_field(ref, "body", blob)
    with pytest.raises(KeyDestroyed):
        await space_key(first.space_id)
    async with transaction() as tx:
        with pytest.raises(KeyDestroyed):
            await create_object_key(tx, first.space_id, "document", uuid.uuid4())
    assert await decrypt_field(elsewhere, "body", other_blob) == b"y"


# --- AC #7: rotation -----------------------------------------------------------------------


def _write_kek(path: Path) -> bytes:
    key = os.urandom(32)
    path.write_bytes(key)
    return key


async def test_rotation_rewraps_space_keys_without_reencrypting(
    pg: PgDatabase, tmp_path: Path, kek_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    refs = [await new_object() for _ in range(3)]
    blobs = [await encrypt_field(r, "title", f"deal {i}") for i, r in enumerate(refs)]
    async with transaction() as tx:
        before = {r.space_id: r.wrapped_key for r in (await tx.execute(select(SpaceKey))).scalars()}
        object_rows_before = sorted(
            (r.object_id, r.wrapped_key) for r in (await tx.execute(select(ObjectKey))).scalars()
        )

    new_path = tmp_path / "kek_v2"
    _write_kek(new_path)
    new_kek = Kek.from_file(new_path, 2)
    assert await rotate.rotate_kek(new_kek, migrator_url=pg.migrator_url) == 3

    async with transaction() as tx:
        after = {r.space_id: r for r in (await tx.execute(select(SpaceKey))).scalars()}
        object_rows_after = sorted(
            (r.object_id, r.wrapped_key) for r in (await tx.execute(select(ObjectKey))).scalars()
        )
    assert {r.kek_version for r in after.values()} == {2}
    assert all(after[s].wrapped_key != before[s] for s in before)
    assert object_rows_after == object_rows_before  # data and object keys untouched

    # The old KEK no longer unwraps anything.
    old = Kek(kek_bytes, 1)
    space_id = next(iter(after))
    with pytest.raises(IntegrityError):
        old.unwrap(after[space_id].wrapped_key, space_key_aad(space_id))
    # Still running on the old KEK (version 1): unwrapping fails closed.
    _cold()
    with pytest.raises(KekError, match="version 2"):
        await decrypt_field(refs[0], "title", blobs[0])

    # With the new KEK installed, old data decrypts.
    monkeypatch.setenv("APP_CRYPTO_KEK_PATH", str(new_path))
    monkeypatch.setenv("APP_CRYPTO_KEK_VERSION", "2")
    reset_crypto()
    for i, (ref, blob) in enumerate(zip(refs, blobs, strict=True)):
        assert await decrypt_field(ref, "title", blob) == f"deal {i}".encode()


async def test_rotation_is_all_or_nothing(pg: PgDatabase, tmp_path: Path) -> None:
    await new_object()
    await new_object()
    stray = await new_space()
    async with transaction() as tx:
        row = (await tx.execute(select(SpaceKey))).scalars().first()
        assert row is not None
    await _as_migrator(
        pg,
        "INSERT INTO space_keys (space_id, wrapped_key, kek_version) VALUES (:s, :w, 7)",
        {"s": stray, "w": row.wrapped_key},
    )
    _write_kek(tmp_path / "k")
    new_kek = Kek.from_file(tmp_path / "k", 2)
    with pytest.raises(KekError, match="version 7"):
        await rotate.rotate_kek(new_kek, migrator_url=pg.migrator_url)
    async with transaction() as tx:
        versions = sorted(r.kek_version for r in (await tx.execute(select(SpaceKey))).scalars())
    assert versions == [1, 1, 7]
    with pytest.raises(KekError, match="greater"):
        await rotate.rotate_kek(Kek(os.urandom(32), 1), migrator_url=pg.migrator_url)


async def test_rotation_cli(pg: PgDatabase, tmp_path: Path, kek_bytes: bytes) -> None:
    await new_object()
    new_path = tmp_path / "kek_v2"
    new_key = _write_kek(new_path)
    env = {
        **os.environ,
        "APP_MIGRATOR_DATABASE_URL": pg.migrator_url,
        "APP_DATABASE_URL": pg.app_url,
    }
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "app.crypto.rotate", "--new-kek", str(new_path)],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "rotate: re-wrapped 1 space key(s) to KEK version 2"
    for secret in (kek_bytes.hex(), new_key.hex()):
        assert secret not in result.stdout + result.stderr
    assert get_kek().version == 1  # this process is untouched
