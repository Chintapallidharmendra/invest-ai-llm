"""Space and object keys (ADR-028): create, wrap, unwrap (with the in-memory cache).

    KEK ──wraps──► space key (space_keys, AAD = space_id)
                     └──wraps──► object key (object_keys, AAD = space_id || type || id)
                                   └──encrypts──► fields and files (app.crypto.fields/files)

A :class:`KeyRef` names an object key by IDs only; it never holds key material. Every
AAD starts with a domain label, so a value sealed for one purpose (say, a wrapped key)
can never be accepted as another (a field). A key that is missing (never created, or
shredded) raises :class:`KeyDestroyed`.

``create_*`` take the caller's session, so a key commits with the object it protects.
Lookups use the caller's session when given, otherwise a short connection of their own,
so they work inside or outside a ``transaction()``.
"""

import re
import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import Row, Select, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.db import get_engine
from app.crypto.cache import get_key_cache
from app.crypto.kek import CryptoError, KekError, get_kek, open_sealed, random_key, seal
from app.crypto.models import ObjectKey, SpaceKey

_OBJECT_TYPE: Final = re.compile(r"[a-z][a-z0-9_]{0,31}")
_SPACE_KEY_AAD: Final = b"invest-ai/space-key/v1\x00"
_OBJECT_KEY_AAD: Final = b"invest-ai/object-key/v1\x00"

Executor = AsyncSession | AsyncConnection


class KeyDestroyed(CryptoError):  # noqa: N818 (the name the spec and callers use)
    """The key is gone (shredded, or never created): the data is unrecoverable."""


@dataclass(frozen=True, slots=True)
class KeyRef:
    """Which object key protects a value: IDs only."""

    space_id: uuid.UUID
    object_type: str
    object_id: uuid.UUID

    def __post_init__(self) -> None:
        if not _OBJECT_TYPE.fullmatch(self.object_type):
            raise ValueError("object_type must match [a-z][a-z0-9_]{0,31}")

    @property
    def cache_key(self) -> tuple[object, ...]:
        return ("object", self.object_type, self.object_id)


def space_key_aad(space_id: uuid.UUID) -> bytes:
    return _SPACE_KEY_AAD + space_id.bytes


def object_key_aad(ref: KeyRef) -> bytes:
    # Fixed-width UUIDs around a NUL-terminated type: no two refs share an encoding.
    return (
        _OBJECT_KEY_AAD
        + ref.space_id.bytes
        + ref.object_type.encode()
        + b"\x00"
        + ref.object_id.bytes
    )


# --- Space keys ---------------------------------------------------------------------------


async def create_space_key(session: AsyncSession, space_id: uuid.UUID) -> bool:
    """Create the space's key (random, wrapped by the KEK). ``False`` if it already exists."""
    kek = get_kek()
    key = random_key()
    inserted = (
        await session.execute(
            insert(SpaceKey)
            .values(
                space_id=space_id,
                wrapped_key=kek.wrap(key, space_key_aad(space_id)),
                kek_version=kek.version,
            )
            .on_conflict_do_nothing(index_elements=[SpaceKey.space_id])
            .returning(SpaceKey.space_id)
        )
    ).first()
    if inserted is not None:
        get_key_cache().put(("space", space_id), key, space_id)
    return inserted is not None


async def _fetch_one[*Ts](executor: Executor | None, stmt: Select[*Ts]) -> Row[*Ts] | None:
    if executor is not None:
        return (await executor.execute(stmt)).first()
    async with get_engine().connect() as conn:
        return (await conn.execute(stmt)).first()


async def space_key(space_id: uuid.UUID, executor: Executor | None = None) -> bytes:
    cache = get_key_cache()
    cached = cache.get(("space", space_id))
    if cached is not None:
        return cached
    row = await _fetch_one(
        executor,
        select(SpaceKey.wrapped_key, SpaceKey.kek_version).where(SpaceKey.space_id == space_id),
    )
    if row is None:
        raise KeyDestroyed("space key not found")
    wrapped, kek_version = row
    kek = get_kek()
    if kek_version != kek.version:
        raise KekError(
            f"space key is wrapped by KEK version {kek_version}, but version {kek.version} is "
            "loaded (set APP_CRYPTO_KEK_VERSION to match the KEK file)"
        )
    key = kek.unwrap(bytes(wrapped), space_key_aad(space_id))
    cache.put(("space", space_id), key, space_id)
    return key


# --- Object keys ---------------------------------------------------------------------------


async def create_object_key(
    session: AsyncSession, space_id: uuid.UUID, object_type: str, object_id: uuid.UUID
) -> KeyRef:
    """Create the object's key (random, wrapped by the space key) and return its ref.

    Idempotent and safe under concurrency: if the object already has a key (in the same
    space), that key is kept and returned.
    """
    ref = KeyRef(space_id, object_type, object_id)
    wrapping_key = await space_key(space_id, session)
    key = random_key()
    inserted = (
        await session.execute(
            insert(ObjectKey)
            .values(
                object_type=object_type,
                object_id=object_id,
                space_id=space_id,
                wrapped_key=seal(wrapping_key, key, object_key_aad(ref)),
            )
            .on_conflict_do_nothing(index_elements=[ObjectKey.object_type, ObjectKey.object_id])
            .returning(ObjectKey.object_id)
        )
    ).first()
    if inserted is None:
        key = await _load_object_key(ref, session)  # the existing key wins
    get_key_cache().put(ref.cache_key, key, space_id)
    return ref


async def _load_object_key(ref: KeyRef, executor: Executor | None) -> bytes:
    row = await _fetch_one(
        executor,
        select(ObjectKey.space_id, ObjectKey.wrapped_key).where(
            ObjectKey.object_type == ref.object_type, ObjectKey.object_id == ref.object_id
        ),
    )
    if row is None:
        raise KeyDestroyed("object key not found")
    stored_space, wrapped = row
    if stored_space != ref.space_id:
        raise CryptoError("the object's key belongs to another space")
    wrapping_key = await space_key(ref.space_id, executor)
    return open_sealed(wrapping_key, bytes(wrapped), object_key_aad(ref))


async def object_key(ref: KeyRef, executor: Executor | None = None) -> bytes:
    """The unwrapped key for ``ref`` (cached up to the TTL)."""
    cache = get_key_cache()
    cached = cache.get(ref.cache_key)
    if cached is not None:
        return cached
    key = await _load_object_key(ref, executor)
    cache.put(ref.cache_key, key, ref.space_id)
    return key
