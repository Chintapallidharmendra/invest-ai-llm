"""Fixtures for the crypto tests: a test KEK in a tmp secret file, fresh caches, and
helpers that create spaces (key rows reference ``spaces`` since Story 8.2), space keys
and object keys."""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.db import transaction
from app.crypto import kek as kek_module
from app.crypto.cache import get_key_cache
from app.crypto.keys import KeyRef, create_object_key, create_space_key
from app.crypto.settings import get_crypto_settings
from tests.conftest import PgDatabase


def reset_crypto() -> None:
    get_crypto_settings.cache_clear()
    kek_module.reset_kek()
    get_key_cache.cache_clear()


@pytest.fixture
def kek_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[bytes]:
    """A random 32-byte KEK at APP_CRYPTO_KEK_PATH (version 1)."""
    key = os.urandom(32)
    path = tmp_path / "kek"
    path.write_bytes(key)
    monkeypatch.setenv("APP_CRYPTO_KEK_PATH", str(path))
    monkeypatch.setenv("APP_CRYPTO_KEK_VERSION", "1")
    reset_crypto()
    yield key
    reset_crypto()


@pytest.fixture
async def crypto_db(pg: PgDatabase, kek_bytes: bytes) -> PgDatabase:
    return pg


async def new_space() -> uuid.UUID:
    """A bare private space (and its invited owner), with no key yet."""
    space_id, owner_id = uuid.uuid4(), uuid.uuid4()
    async with transaction() as tx:
        await tx.execute(
            text(
                "INSERT INTO users (id, username, email, role, status) "
                "VALUES (:id, :name, :email, 'user', 'invited')"
            ),
            {"id": owner_id, "name": f"u{owner_id.hex[:12]}", "email": f"{owner_id.hex}@t.test"},
        )
        await tx.execute(
            text("INSERT INTO spaces (id, kind, owner_user_id) VALUES (:id, 'private', :owner)"),
            {"id": space_id, "owner": owner_id},
        )
    return space_id


async def new_object(space_id: uuid.UUID | None = None, object_type: str = "document") -> KeyRef:
    space_id = space_id or await new_space()
    async with transaction() as tx:
        await create_space_key(tx, space_id)
        return await create_object_key(tx, space_id, object_type, uuid.uuid4())


def cached_ref(key: bytes | None = None, object_type: str = "document") -> tuple[KeyRef, bytes]:
    """A ref whose key is only in the cache (unit tests without a database)."""
    key = key or os.urandom(32)
    ref = KeyRef(uuid.uuid4(), object_type, uuid.uuid4())
    get_key_cache().put(ref.cache_key, key, ref.space_id)
    return ref, key
