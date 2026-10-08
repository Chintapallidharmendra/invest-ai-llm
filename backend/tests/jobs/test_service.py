"""AC #6: progress and owner-only reads."""

import uuid

import pytest
from valkey.asyncio import Valkey

from app.core import readiness
from app.jobs import queue, service
from app.jobs.queue import Runner
from app.jobs.semaphore import GpuSemaphore
from tests.conftest import PgDatabase
from tests.jobs.conftest import USER_A, USER_B, FakeClock, Recorder, fetch_job, make_settings


async def test_owner_sees_the_job_others_get_nothing(
    jobs_db: PgDatabase, job_types: Recorder
) -> None:
    space = uuid.uuid4()
    job_id = await queue.enqueue("noop", {"n": 1}, user_id=USER_A, space_id=space)
    system_job = await queue.enqueue("noop", {})

    view = await service.get_job_for_owner(job_id, USER_A)
    assert view is not None
    assert (view.id, view.type, view.status, view.space_id) == (job_id, "noop", "queued", space)
    assert not hasattr(view, "payload")

    assert await service.get_job_for_owner(job_id, USER_B) is None
    assert await service.get_job_for_owner(uuid.uuid4(), USER_A) is None
    assert await service.get_job_for_owner(system_job, USER_A) is None


async def test_progress_from_a_handler(
    jobs_db: PgDatabase, job_types: Recorder, valkey: Valkey, clock: FakeClock
) -> None:
    job_id = await queue.enqueue("heavy_sleep", {"ms": 0}, user_id=USER_A)
    clock.advance(1)
    sem = GpuSemaphore(valkey, limit=1, ttl_s=30)
    await Runner(make_settings(), semaphore=sem, clock=clock).drain()
    view = await service.get_job_for_owner(job_id, USER_A)
    assert view is not None
    assert view.progress == {"done": 2, "total": 2}
    assert view.status == "succeeded"


async def test_progress_validation_and_running_only(
    jobs_db: PgDatabase, job_types: Recorder
) -> None:
    job_id = await queue.enqueue("noop", {})
    with pytest.raises(ValueError, match="progress"):
        await service.update_progress(job_id, 3, 2)
    with pytest.raises(ValueError, match="progress"):
        await service.update_progress(job_id, -1, 2)
    await service.update_progress(job_id, 1, 2)  # queued: ignored
    assert (await fetch_job(job_id)).progress is None


async def test_readiness_checks(jobs_db: PgDatabase, valkey: Valkey) -> None:
    service.register_checks(GpuSemaphore(valkey, limit=1, ttl_s=1))
    service.register_checks(GpuSemaphore(valkey, limit=1, ttl_s=1))  # idempotent
    try:
        assert await readiness.failing_checks() == []
    finally:
        service.unregister_checks()
