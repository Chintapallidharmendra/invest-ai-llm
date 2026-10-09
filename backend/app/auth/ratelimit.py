"""Fixed-window rate limits in Valkey (ADR-018).

Keys hold a keyed hash of the subject, never the raw value (Valkey stores no PII), and
expire with their window. When Valkey can't be reached the limiter fails closed with
:class:`RateLimitUnavailableError`.
"""

import time
from dataclasses import dataclass

from valkey.asyncio import Valkey
from valkey.exceptions import ValkeyError

from app.audit.types import KeyedHash
from app.jobs.settings import get_jobs_settings

_TIMEOUT_S = 2.0


class RateLimitUnavailableError(RuntimeError):
    """Valkey didn't answer; callers refuse the request (fail closed)."""


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    count: int
    retry_after_s: int  # seconds until the window resets (meaningful when not allowed)


async def hit(
    scope: str,
    subject: str,
    *,
    limit: int,
    window_s: int,
    now: float | None = None,
    url: str | None = None,
) -> RateLimitResult:
    """Count one attempt by ``subject`` in ``scope``; allowed while within ``limit``."""
    now = time.time() if now is None else now
    window = int(now // window_s)
    key = f"rl:{scope}:{KeyedHash.of(f'{scope}:{subject}')}:{window}"
    retry_after = max(1, int((window + 1) * window_s - now + 0.999))
    url = url or get_jobs_settings().valkey_url.get_secret_value()
    try:
        async with (
            Valkey.from_url(
                url, socket_timeout=_TIMEOUT_S, socket_connect_timeout=_TIMEOUT_S
            ) as client,
            client.pipeline(transaction=True) as pipe,
        ):
            pipe.incr(key)
            pipe.expire(key, window_s + 1)
            count, _ = await pipe.execute()
    except (ValkeyError, OSError) as exc:
        raise RateLimitUnavailableError(type(exc).__name__) from None
    return RateLimitResult(int(count) <= limit, int(count), retry_after)
