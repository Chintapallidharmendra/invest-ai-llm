"""In-memory cache of unwrapped keys: TTL (at most 5 minutes) and size-bounded (LRU).

Entries are keyed by ``("space", space_id)`` or ``("object", object_type, object_id)``
and remember their space, so :meth:`KeyCache.evict_space` drops a space key together
with every object key under it. Shredding evicts here; other processes' caches expire
within the TTL.
"""

import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock

from app.crypto.settings import get_crypto_settings


@dataclass(slots=True)
class _Entry:
    key: bytes
    space_id: uuid.UUID
    expires_at: float


class KeyCache:
    def __init__(
        self,
        ttl_s: float,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[tuple[object, ...], _Entry] = OrderedDict()
        self._lock = Lock()

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, cache_key: tuple[object, ...]) -> bytes | None:
        with self._lock:
            entry = self._entries.get(cache_key)
            if entry is None:
                return None
            if entry.expires_at <= self._clock():
                del self._entries[cache_key]
                return None
            self._entries.move_to_end(cache_key)
            return entry.key

    def put(self, cache_key: tuple[object, ...], key: bytes, space_id: uuid.UUID) -> None:
        with self._lock:
            self._entries[cache_key] = _Entry(key, space_id, self._clock() + self.ttl_s)
            self._entries.move_to_end(cache_key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def evict(self, cache_key: tuple[object, ...]) -> None:
        with self._lock:
            self._entries.pop(cache_key, None)

    def evict_space(self, space_id: uuid.UUID) -> None:
        with self._lock:
            for cache_key in [k for k, e in self._entries.items() if e.space_id == space_id]:
                del self._entries[cache_key]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


@lru_cache(maxsize=1)
def get_key_cache() -> KeyCache:
    settings = get_crypto_settings()
    return KeyCache(settings.key_cache_ttl_s, settings.key_cache_max_entries)
