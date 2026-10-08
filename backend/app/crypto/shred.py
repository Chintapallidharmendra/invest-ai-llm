"""Crypto-shredding (ADR-028, ADR-035): delete key rows, evict cached keys.

Step 2 of the delete pipeline: once an object's key row is gone (and the 30-day backups
that still hold the wrapped key have rolled over), its ciphertext is unrecoverable.
Deletion happens in the caller's transaction; this process's cache is evicted at once,
other processes' caches expire within ``key_cache_ttl_s`` (at most 5 minutes).
"""

import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto.cache import get_key_cache
from app.crypto.keys import KeyRef
from app.crypto.models import ObjectKey, SpaceKey


def _rowcount(result: object) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


async def shred_object(session: AsyncSession, object_type: str, object_id: uuid.UUID) -> bool:
    """Destroy one object's key; ``False`` if it had none."""
    result = await session.execute(
        delete(ObjectKey).where(
            ObjectKey.object_type == object_type, ObjectKey.object_id == object_id
        )
    )
    # KeyRef validates object_type; the space is irrelevant for the cache key.
    get_key_cache().evict(KeyRef(uuid.UUID(int=0), object_type, object_id).cache_key)
    return _rowcount(result) == 1


async def shred_space(session: AsyncSession, space_id: uuid.UUID) -> int:
    """Destroy a space's key and every object key under it; returns object keys removed."""
    result = await session.execute(delete(ObjectKey).where(ObjectKey.space_id == space_id))
    await session.execute(delete(SpaceKey).where(SpaceKey.space_id == space_id))
    get_key_cache().evict_space(space_id)
    return _rowcount(result)
