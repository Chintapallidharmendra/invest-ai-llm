"""GPU semaphore for LLM-heavy jobs (ADR-029), held in Valkey across all workers.

Holders live in a sorted set scored by expiry time (Valkey server time, so worker clocks
don't matter). Expired holders are dropped before every acquire, so a crashed worker's
slot frees itself after ``ttl_s``. Valkey is never the source of truth (ADR-018): the
semaphore is advisory and job state stays in Postgres. When Valkey is unreachable,
:meth:`GpuSemaphore.acquire` returns ``False`` and heavy jobs simply wait.
"""

import uuid
from typing import Final, cast

from valkey.asyncio import Valkey
from valkey.exceptions import ValkeyError

from app.core.obs import exc_summary, get_logger

DEFAULT_KEY: Final = "jobs:gpu_semaphore"

# KEYS[1] = set; ARGV = holder, limit, ttl_ms. Returns 1 when a slot was taken.
_ACQUIRE: Final = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZSCORE', KEYS[1], ARGV[1]) then
  redis.call('ZADD', KEYS[1], now + tonumber(ARGV[3]), ARGV[1])
  return 1
end
if redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[2]) then
  redis.call('ZADD', KEYS[1], now + tonumber(ARGV[3]), ARGV[1])
  redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[3]) * 2)
  return 1
end
return 0
"""

# Extends a live holder; returns 0 when the slot already expired (it is not re-taken).
_RENEW: Final = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local score = redis.call('ZSCORE', KEYS[1], ARGV[1])
if not score or tonumber(score) <= now then
  redis.call('ZREM', KEYS[1], ARGV[1])
  return 0
end
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[1])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]) * 2)
return 1
"""

# Live holders (expired ones are not counted).
_COUNT: Final = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
return redis.call('ZCOUNT', KEYS[1], '(' .. now, '+inf')
"""

_log = get_logger("jobs.semaphore")


class GpuSemaphore:
    def __init__(self, client: Valkey, *, limit: int, ttl_s: float, key: str = DEFAULT_KEY) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self._client = client
        self.limit = limit
        self._ttl_ms = max(1, int(ttl_s * 1000))
        self._key = key

    @staticmethod
    def new_holder() -> str:
        return str(uuid.uuid4())

    async def acquire(self, holder: str) -> bool:
        """Take a slot for ``holder``; ``False`` when full or Valkey is unreachable."""
        try:
            result = await self._client.eval(  # type: ignore[misc]
                _ACQUIRE, 1, self._key, holder, str(self.limit), str(self._ttl_ms)
            )
        except (ValkeyError, OSError) as exc:
            _log.warning("jobs.semaphore_unavailable", **exc_summary(exc))
            return False
        return bool(result)

    async def renew(self, holder: str) -> bool:
        """Extend the slot's TTL; ``False`` if it expired or Valkey is unreachable."""
        try:
            result = await self._client.eval(  # type: ignore[misc]
                _RENEW, 1, self._key, holder, str(self._ttl_ms)
            )
        except (ValkeyError, OSError) as exc:
            _log.warning("jobs.semaphore_unavailable", **exc_summary(exc))
            return False
        return bool(result)

    async def release(self, holder: str) -> None:
        try:
            await self._client.zrem(self._key, holder)
        except (ValkeyError, OSError) as exc:
            # The TTL frees the slot anyway.
            _log.warning("jobs.semaphore_unavailable", **exc_summary(exc))

    async def held(self) -> int:
        """Number of live holders (for tests and metrics)."""
        return cast(int, await self._client.eval(_COUNT, 1, self._key))  # type: ignore[misc]

    async def ping(self) -> bool:
        return bool(await self._client.ping())
