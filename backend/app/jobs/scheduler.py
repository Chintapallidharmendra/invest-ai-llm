"""Cron registry for the worker's APScheduler 3.x ``AsyncIOScheduler`` (ADR-017).

Feature modules register crons at import time, in ``app/<module>/jobs.py``::

    register_cron("audit_verify_nightly", "30 2 * * *", enqueue_audit_verify)

Expressions are standard 5-field crontab, evaluated in ``Asia/Kolkata`` by default.
Long work should ``enqueue()`` a job rather than run in the cron callback, so it gets
leases, retries and the GPU semaphore. A failing callback is logged (type and location
only) and the next run still fires.
"""

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final
from zoneinfo import ZoneInfo

import structlog

# APScheduler 3.x ships no type information.
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]

from app.core.ids import new_uuid7
from app.core.obs import exc_summary, get_logger

DEFAULT_TIMEZONE: Final = "Asia/Kolkata"

CronFunc = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class CronJob:
    name: str
    cron_expr: str
    func: CronFunc


_crons: dict[str, CronJob] = {}
_log = get_logger("jobs.scheduler")


def register_cron(name: str, cron_expr: str, func: CronFunc) -> CronJob:
    """Register a cron; raises ``ValueError`` on a bad expression or duplicate name."""
    if name in _crons:
        raise ValueError(f"cron already registered: {name}")
    CronTrigger.from_crontab(cron_expr, timezone=ZoneInfo(DEFAULT_TIMEZONE))  # validates
    cron = CronJob(name, cron_expr, func)
    _crons[name] = cron
    return cron


def unregister_cron(name: str) -> None:
    _crons.pop(name, None)


def registered_crons() -> Mapping[str, CronJob]:
    return dict(_crons)


def _wrap(cron: CronJob) -> CronFunc:
    module = f"cron.{cron.name}"

    async def run() -> None:
        started = time.monotonic()
        with structlog.contextvars.bound_contextvars(correlation_id=str(new_uuid7())):
            try:
                await cron.func()
            except Exception as exc:
                _log.error("cron.failed", module=module, **exc_summary(exc))
                return
            _log.info(
                "cron.succeeded",
                module=module,
                latency_ms=round((time.monotonic() - started) * 1000),
            )

    return run


def build_scheduler(
    timezone: str = DEFAULT_TIMEZONE, crons: Mapping[str, CronJob] | None = None
) -> AsyncIOScheduler:
    """An (unstarted) scheduler with every registered cron.

    Each cron runs at most once at a time; missed runs are coalesced into one, and a
    run more than 5 minutes late is skipped.
    """
    tz = ZoneInfo(timezone)
    scheduler = AsyncIOScheduler(timezone=tz)
    for cron in (crons if crons is not None else _crons).values():
        scheduler.add_job(
            _wrap(cron),
            CronTrigger.from_crontab(cron.cron_expr, timezone=tz),
            id=cron.name,
            name=cron.name,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
            replace_existing=True,
        )
    return scheduler
