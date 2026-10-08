"""Job reads and progress for other modules, plus the worker's readiness checks.

The ``jobs`` table has no RLS (it holds no content), so **user-facing reads must go
through** :func:`get_job_for_owner`, which returns nothing unless the user owns the job.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import select, text, update

from app.core import readiness
from app.core.db import transaction
from app.core.timeutil import utcnow
from app.jobs.models import Job, JobStatus
from app.jobs.semaphore import GpuSemaphore

CHECK_DB: Final = "jobs.db"
CHECK_VALKEY: Final = "jobs.valkey"


@dataclass(frozen=True)
class JobView:
    """What a job's owner may see: status and progress, never the payload."""

    id: uuid.UUID
    type: str
    status: JobStatus
    space_id: uuid.UUID | None
    progress: dict[str, Any] | None
    attempts: int
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


async def update_progress(job_id: uuid.UUID, done: int, total: int) -> None:
    """Record ``done`` of ``total`` steps (``jobs.progress = {"done", "total"}``)."""
    if total < 0 or done < 0 or done > total:
        raise ValueError("progress must satisfy 0 <= done <= total")
    now = utcnow()
    async with transaction() as tx:
        await tx.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.RUNNING)
            .values(progress={"done": done, "total": total}, updated_at=now)
        )


async def get_job_for_owner(job_id: uuid.UUID, user_id: uuid.UUID) -> JobView | None:
    """The job if ``user_id`` owns it; ``None`` otherwise (callers answer 404)."""
    async with transaction() as tx:
        job = (
            await tx.execute(select(Job).where(Job.id == job_id, Job.user_id == user_id))
        ).scalar_one_or_none()
    if job is None:
        return None
    return JobView(
        id=job.id,
        type=job.type,
        status=job.status,
        space_id=job.space_id,
        progress=job.progress,
        attempts=job.attempts,
        error_code=job.error_code,
        created_at=job.created_at,
        updated_at=job.updated_at,
        finished_at=job.finished_at,
    )


async def _db_ok() -> bool:
    async with transaction() as tx:
        await tx.execute(text("SELECT 1"))
    return True


def register_checks(semaphore: GpuSemaphore | None) -> None:
    """Add ``jobs.db`` and (with a semaphore) ``jobs.valkey`` to ``/readyz``. Idempotent."""
    unregister_checks()
    readiness.register_check(CHECK_DB, _db_ok)
    if semaphore is not None:
        readiness.register_check(CHECK_VALKEY, semaphore.ping)


def unregister_checks() -> None:
    readiness.unregister_check(CHECK_DB)
    readiness.unregister_check(CHECK_VALKEY)
