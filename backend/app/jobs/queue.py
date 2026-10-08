"""Postgres job queue and runner (ADR-017, ADR-029).

- **Claim:** ``SELECT … FOR UPDATE SKIP LOCKED`` ordered by ``priority, run_after``
  (lower priority runs first). A claim sets ``status=running``, a lease
  (``lease_until``, default 120 s) and a fresh ``lease_token``; heartbeats renew it.
  Every later write checks the token, so a runner that lost its lease can't finish
  a job another runner re-claimed.
- **LLM-heavy jobs** first take a slot in the Valkey :class:`GpuSemaphore`, then claim
  round-robin across ``user_id``: the oldest job of the user whose last heavy job
  started least recently (never-served users first).
- **Failures** retry with exponential backoff until ``max_attempts``, then ``failed``.
  Only an ``error_code`` is stored; exception messages never reach the DB or logs.
- **Expired leases** (crashed workers) are re-queued at start-up and every
  ``reap_interval_s``; they count as an attempt, so a job that kills its worker ends up
  ``failed`` instead of looping.
- **Shutdown:** the runner stops claiming, gives in-flight jobs ``shutdown_grace_s`` to
  finish, then cancels them and releases their leases (the attempt is not counted).

All times come from an injectable clock, so tests control them.
"""

import asyncio
import re
import uuid
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

import structlog
from pydantic import BaseModel, ValidationError
from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.db import transaction
from app.core.ids import new_uuid7
from app.core.obs import current_correlation_id, exc_summary, get_logger
from app.core.timeutil import utcnow
from app.jobs import registry
from app.jobs.models import DEFAULT_MAX_ATTEMPTS, DEFAULT_PRIORITY, Job, JobStatus
from app.jobs.registry import JobContext, JobError, JobType
from app.jobs.semaphore import GpuSemaphore
from app.jobs.service import update_progress
from app.jobs.settings import JobsSettings

Clock = Callable[[], datetime]

ERROR_CODE_PATTERN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}")
UNHANDLED_ERROR: Final = "unhandled_exception"
INVALID_ERROR_CODE: Final = "job_error"
INVALID_PAYLOAD: Final = "invalid_payload"
LEASE_EXPIRED: Final = "lease_expired"
# Heavy-job fairness looks at starts within this window; older users count as unserved.
FAIRNESS_WINDOW: Final = timedelta(days=1)

_log = get_logger("jobs")


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    type: str
    user_id: uuid.UUID | None
    space_id: uuid.UUID | None
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    correlation_id: str | None
    lease_token: uuid.UUID


def _claimed(job: Job) -> ClaimedJob:
    assert job.lease_token is not None  # noqa: S101 (set by the claim itself)
    return ClaimedJob(
        id=job.id,
        type=job.type,
        user_id=job.user_id,
        space_id=job.space_id,
        payload=dict(job.payload),
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        correlation_id=job.correlation_id,
        lease_token=job.lease_token,
    )


def safe_error_code(code: str) -> str:
    """Error codes are short machine identifiers; anything else is replaced."""
    return code if ERROR_CODE_PATTERN.fullmatch(code) else INVALID_ERROR_CODE


def backoff_delay(attempt: int, *, base_s: float, max_s: float) -> timedelta:
    """Delay before retry number ``attempt`` (1-based): base * 2^(attempt-1), capped."""
    return timedelta(seconds=min(max_s, base_s * 2 ** max(0, attempt - 1)))


def _rowcount(result: Any) -> int:
    return result.rowcount if isinstance(result, CursorResult) else 0


# --- Enqueue -----------------------------------------------------------------------


async def enqueue(
    type: str,
    payload: BaseModel | Mapping[str, Any],
    *,
    user_id: uuid.UUID | None = None,
    space_id: uuid.UUID | None = None,
    priority: int = DEFAULT_PRIORITY,
    run_after: datetime | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    session: AsyncSession | None = None,
) -> uuid.UUID:
    """Validate ``payload`` against the type's model and queue the job.

    Pass ``session`` to enqueue inside the caller's transaction (e.g. together with the
    row the job will process); otherwise a transaction is opened.
    """
    job_type = registry.get(type)
    data = registry.validate_payload(job_type, payload)
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    job = Job(
        id=new_uuid7(),
        type=type,
        user_id=user_id,
        space_id=space_id,
        payload=data,
        priority=priority,
        status=JobStatus.QUEUED,
        attempts=0,
        max_attempts=max_attempts,
        run_after=run_after or utcnow(),
        correlation_id=current_correlation_id(),
    )
    if session is not None:
        session.add(job)
        await session.flush()
    else:
        async with transaction() as tx:
            tx.add(job)
    return job.id


# --- Claim -------------------------------------------------------------------------


def _claim_values(now: datetime, lease_s: float) -> dict[str, Any]:
    return {
        "status": JobStatus.RUNNING,
        "attempts": Job.attempts + 1,
        "lease_until": now + timedelta(seconds=lease_s),
        "lease_token": uuid.uuid4(),
        "started_at": now,
        "updated_at": now,
    }


async def claim(types: Collection[str], *, now: datetime, lease_s: float) -> ClaimedJob | None:
    """Claim the next runnable job of ``types`` (priority, then run_after)."""
    if not types:
        return None
    candidate = aliased(Job)
    pick = (
        select(candidate.id)
        .where(
            candidate.status == JobStatus.QUEUED,
            candidate.run_after <= now,
            candidate.type.in_(list(types)),
        )
        .order_by(candidate.priority, candidate.run_after, candidate.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    stmt = update(Job).where(Job.id == pick).values(_claim_values(now, lease_s)).returning(Job)
    async with transaction() as tx:
        job = (await tx.execute(stmt)).scalar_one_or_none()
        return _claimed(job) if job is not None else None


async def claim_heavy(
    types: Collection[str], *, now: datetime, lease_s: float
) -> ClaimedJob | None:
    """Claim the next heavy job, round-robin across users (ADR-029).

    Order: priority, then the user whose last heavy start is oldest (never served
    first), then that user's oldest job.
    """
    if not types:
        return None
    type_list = list(types)
    started = aliased(Job)
    served = (
        select(started.user_id, func.max(started.started_at).label("last_started"))
        .where(
            started.type.in_(type_list),
            started.started_at.is_not(None),
            started.started_at >= now - FAIRNESS_WINDOW,
        )
        .group_by(started.user_id)
        .cte("served")
    )
    candidate = aliased(Job)
    pick = (
        select(candidate.id)
        .outerjoin(served, served.c.user_id.is_not_distinct_from(candidate.user_id))
        .where(
            candidate.status == JobStatus.QUEUED,
            candidate.run_after <= now,
            candidate.type.in_(type_list),
        )
        .order_by(
            candidate.priority,
            served.c.last_started.asc().nulls_first(),
            candidate.run_after,
            candidate.id,
        )
        .limit(1)
        .with_for_update(of=candidate, skip_locked=True)
        .scalar_subquery()
    )
    stmt = update(Job).where(Job.id == pick).values(_claim_values(now, lease_s)).returning(Job)
    async with transaction() as tx:
        job = (await tx.execute(stmt)).scalar_one_or_none()
        return _claimed(job) if job is not None else None


# --- Lease and outcome ---------------------------------------------------------------


def _owned(job_id: uuid.UUID, token: uuid.UUID) -> Any:
    return (Job.id == job_id) & (Job.lease_token == token) & (Job.status == JobStatus.RUNNING)


async def heartbeat(job_id: uuid.UUID, token: uuid.UUID, *, now: datetime, lease_s: float) -> bool:
    """Extend the lease; ``False`` when it was lost (expired and re-claimed or reaped)."""
    stmt = (
        update(Job)
        .where(_owned(job_id, token))
        .values(lease_until=now + timedelta(seconds=lease_s), updated_at=now)
    )
    async with transaction() as tx:
        return _rowcount(await tx.execute(stmt)) == 1


async def complete(job_id: uuid.UUID, token: uuid.UUID, *, now: datetime) -> bool:
    stmt = (
        update(Job)
        .where(_owned(job_id, token))
        .values(
            status=JobStatus.SUCCEEDED,
            lease_until=None,
            lease_token=None,
            error_code=None,
            finished_at=now,
            updated_at=now,
        )
    )
    async with transaction() as tx:
        return _rowcount(await tx.execute(stmt)) == 1


async def fail(
    job_id: uuid.UUID,
    token: uuid.UUID,
    *,
    now: datetime,
    error_code: str,
    retry: bool,
    settings: JobsSettings,
) -> JobStatus | None:
    """Record a failure: re-queue with backoff, or ``failed`` when out of attempts.

    Returns the new status, or ``None`` if the lease was already lost.
    """
    code = safe_error_code(error_code)
    async with transaction() as tx:
        row = (
            await tx.execute(
                select(Job.attempts, Job.max_attempts)
                .where(_owned(job_id, token))
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            return None
        attempts, max_attempts = row
        if retry and attempts < max_attempts:
            delay = backoff_delay(
                attempts, base_s=settings.backoff_base_s, max_s=settings.backoff_max_s
            )
            status = JobStatus.QUEUED
            values: dict[str, Any] = {"run_after": now + delay}
        else:
            status = JobStatus.FAILED
            values = {"finished_at": now}
        await tx.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status=status,
                error_code=code,
                lease_until=None,
                lease_token=None,
                updated_at=now,
                **values,
            )
        )
        return status


async def release(job_id: uuid.UUID, token: uuid.UUID, *, now: datetime) -> bool:
    """Give an interrupted job back to the queue without counting the attempt."""
    stmt = (
        update(Job)
        .where(_owned(job_id, token))
        .values(
            status=JobStatus.QUEUED,
            attempts=func.greatest(Job.attempts - 1, 0),
            lease_until=None,
            lease_token=None,
            run_after=now,
            updated_at=now,
        )
    )
    async with transaction() as tx:
        return _rowcount(await tx.execute(stmt)) == 1


async def requeue_expired(*, now: datetime) -> int:
    """Re-queue ``running`` jobs whose lease expired (``failed`` when out of attempts)."""
    out_of_attempts = Job.attempts >= Job.max_attempts
    expired = (Job.status == JobStatus.RUNNING) & (Job.lease_until < now)
    async with transaction() as tx:
        failed = await tx.execute(
            update(Job)
            .where(expired, out_of_attempts)
            .values(
                status=JobStatus.FAILED,
                error_code=LEASE_EXPIRED,
                lease_until=None,
                lease_token=None,
                finished_at=now,
                updated_at=now,
            )
        )
        requeued = await tx.execute(
            update(Job)
            .where(expired, ~out_of_attempts)
            .values(
                status=JobStatus.QUEUED,
                error_code=LEASE_EXPIRED,
                lease_until=None,
                lease_token=None,
                run_after=now,
                updated_at=now,
            )
        )
        return _rowcount(failed) + _rowcount(requeued)


# --- Runner ----------------------------------------------------------------------------


class Runner:
    """Claims and runs jobs for the job types registered in this process."""

    def __init__(
        self,
        settings: JobsSettings,
        *,
        semaphore: GpuSemaphore | None,
        clock: Clock = utcnow,
        job_types: Mapping[str, JobType[Any]] | None = None,
    ) -> None:
        self._settings = settings
        self._semaphore = semaphore
        self._clock = clock
        self._types = dict(job_types) if job_types is not None else dict(registry.registered())
        self._light = [n for n, t in self._types.items() if not t.llm_heavy]
        self._heavy = [n for n, t in self._types.items() if t.llm_heavy]
        self._tasks: set[asyncio.Task[None]] = set()
        self._lease_lost: set[uuid.UUID] = set()

    @property
    def in_flight(self) -> int:
        return len(self._tasks)

    async def reap(self) -> int:
        count = await requeue_expired(now=self._clock())
        if count:
            _log.warning("jobs.leases_expired", requeued_count=count)
        return count

    async def run(self, stop: asyncio.Event) -> None:
        """Run until ``stop`` is set, then shut down gracefully."""
        loop = asyncio.get_running_loop()
        stop_wait = asyncio.ensure_future(stop.wait())
        next_reap = 0.0
        try:
            while not stop.is_set():
                try:
                    if loop.time() >= next_reap:
                        await self.reap()
                        next_reap = loop.time() + self._settings.reap_interval_s
                    await self.fill()
                except Exception as exc:  # DB down etc.: keep the worker alive
                    _log.warning("jobs.poll_failed", **exc_summary(exc))
                await asyncio.wait(
                    {stop_wait, *self._tasks},
                    timeout=self._settings.poll_interval_s,
                    return_when=asyncio.FIRST_COMPLETED,
                )
        finally:
            stop_wait.cancel()
            await self.shutdown()

    async def fill(self) -> int:
        """Claim jobs until the concurrency limit or the queue is exhausted."""
        started = 0
        while len(self._tasks) < self._settings.concurrency:
            claimed = await self._claim_next()
            if claimed is None:
                break
            job, holder = claimed
            task = asyncio.create_task(self._execute(job, holder))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            started += 1
        return started

    async def drain(self) -> None:
        """Run everything runnable now, then return (for tests and one-shot use)."""
        while True:
            await self.fill()
            if not self._tasks:
                return
            await asyncio.wait(set(self._tasks), return_when=asyncio.FIRST_COMPLETED)

    async def shutdown(self) -> None:
        if not self._tasks:
            return
        _, pending = await asyncio.wait(set(self._tasks), timeout=self._settings.shutdown_grace_s)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    async def _claim_next(self) -> tuple[ClaimedJob, str | None] | None:
        now = self._clock()
        lease_s = self._settings.lease_s
        if self._heavy and self._semaphore is not None:
            holder = self._semaphore.new_holder()
            if await self._semaphore.acquire(holder):
                try:
                    job = await claim_heavy(self._heavy, now=now, lease_s=lease_s)
                except BaseException:
                    await self._semaphore.release(holder)
                    raise
                if job is not None:
                    return job, holder
                await self._semaphore.release(holder)
        job = await claim(self._light, now=now, lease_s=lease_s)
        return (job, None) if job is not None else None

    async def _execute(self, job: ClaimedJob, holder: str | None) -> None:
        job_type = self._types[job.type]
        module = f"jobs.{job.type}"
        loop = asyncio.get_running_loop()
        started = loop.time()
        current = asyncio.current_task()
        assert current is not None  # noqa: S101
        beat = asyncio.create_task(self._heartbeat(job, holder, current))
        with structlog.contextvars.bound_contextvars(
            correlation_id=job.correlation_id or str(new_uuid7()),
            user_id=str(job.user_id) if job.user_id else None,
            space_id=str(job.space_id) if job.space_id else None,
        ):
            _log.info("job.started", module=module)
            try:
                try:
                    payload = job_type.payload_model.model_validate(job.payload)
                except ValidationError:
                    raise registry.PermanentJobError(INVALID_PAYLOAD) from None
                ctx = JobContext(
                    job_id=job.id,
                    job_type=job.type,
                    user_id=job.user_id,
                    space_id=job.space_id,
                    attempt=job.attempts,
                    max_attempts=job.max_attempts,
                    correlation_id=job.correlation_id,
                    _progress=update_progress,
                )
                await job_type.handler(ctx, payload)
            except asyncio.CancelledError:
                if job.id in self._lease_lost:
                    _log.warning("job.lease_lost", module=module)
                else:
                    await release(job.id, job.lease_token, now=self._clock())
                    _log.info("job.released", module=module)
                raise
            except JobError as exc:
                await self._record_failure(job, module, exc.code, retry=exc.retry, exc=None)
            except Exception as exc:
                await self._record_failure(job, module, UNHANDLED_ERROR, retry=True, exc=exc)
            else:
                if await complete(job.id, job.lease_token, now=self._clock()):
                    _log.info(
                        "job.succeeded",
                        module=module,
                        latency_ms=round((loop.time() - started) * 1000),
                    )
                else:
                    _log.warning("job.lease_lost", module=module)
            finally:
                beat.cancel()
                self._lease_lost.discard(job.id)
                if holder is not None and self._semaphore is not None:
                    await self._semaphore.release(holder)

    async def _record_failure(
        self,
        job: ClaimedJob,
        module: str,
        code: str,
        *,
        retry: bool,
        exc: BaseException | None,
    ) -> None:
        status = await fail(
            job.id,
            job.lease_token,
            now=self._clock(),
            error_code=code,
            retry=retry,
            settings=self._settings,
        )
        summary = exc_summary(exc) if exc is not None else {}
        error_code = safe_error_code(code)
        if status is JobStatus.QUEUED:
            _log.warning("job.retry_scheduled", module=module, error_code=error_code, **summary)
        elif status is JobStatus.FAILED:
            _log.error("job.failed", module=module, error_code=error_code, **summary)
        else:
            _log.warning("job.lease_lost", module=module, error_code=error_code)

    async def _heartbeat(
        self, job: ClaimedJob, holder: str | None, task: asyncio.Task[Any]
    ) -> None:
        while True:
            await asyncio.sleep(self._settings.heartbeat_s)
            try:
                alive = await heartbeat(
                    job.id, job.lease_token, now=self._clock(), lease_s=self._settings.lease_s
                )
            except Exception as exc:
                _log.warning("jobs.heartbeat_failed", **exc_summary(exc))
                continue
            if not alive:
                self._lease_lost.add(job.id)
                task.cancel()
                return
            if holder is not None and self._semaphore is not None:
                await self._semaphore.renew(holder)
