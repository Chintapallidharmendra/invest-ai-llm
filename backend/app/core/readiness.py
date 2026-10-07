"""Readiness check registry (ADR-033).

Each module registers its own named async check (e.g. vLLM, parser, KEK loaded). A
check fails if it raises, returns ``False`` or times out. A failing safety dependency
makes ``/readyz`` return 503 so the system refuses work.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Final

from app.core.obs import exc_summary, get_logger

Check = Callable[[], Awaitable[bool | None]]

DEFAULT_TIMEOUT_S: Final = 2.0

_checks: dict[str, Check] = {}
_log = get_logger("core.readiness")


def register_check(name: str, check: Check) -> None:
    if name in _checks:
        raise ValueError(f"readiness check already registered: {name}")
    _checks[name] = check


def unregister_check(name: str) -> None:
    _checks.pop(name, None)


async def _passes(name: str, check: Check, timeout_s: float) -> bool:
    try:
        result = await asyncio.wait_for(check(), timeout=timeout_s)
    except Exception as exc:  # any failure means not ready
        _log.warning("readiness.check_failed", **exc_summary(exc))
        return False
    return result is not False


async def failing_checks(timeout_s: float = DEFAULT_TIMEOUT_S) -> list[str]:
    """Run all checks concurrently; return the names of those that failed."""
    names = sorted(_checks)
    results = await asyncio.gather(*(_passes(n, _checks[n], timeout_s) for n in names))
    return [name for name, ok in zip(names, results, strict=True) if not ok]
