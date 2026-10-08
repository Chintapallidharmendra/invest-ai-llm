"""AC #4: heavy jobs: concurrency cap and round-robin across users."""

import asyncio

from valkey.asyncio import Valkey

from app.core import readiness
from app.jobs import queue, service
from app.jobs.models import JobStatus
from app.jobs.queue import Runner
from app.jobs.semaphore import GpuSemaphore
from tests.conftest import PgDatabase
from tests.jobs.conftest import USER_A, USER_B, USER_C, Recorder, all_jobs, make_settings


async def _enqueue(user: object, count: int, ms: int = 0) -> None:
    for _ in range(count):
        await queue.enqueue("heavy_sleep", {"ms": ms}, user_id=user)  # type: ignore[arg-type]


async def test_round_robin_across_users(
    jobs_db: PgDatabase, job_types: Recorder, valkey: Valkey
) -> None:
    await _enqueue(USER_A, 5)
    await _enqueue(USER_B, 1)
    sem = GpuSemaphore(valkey, limit=1, ttl_s=30)
    await Runner(make_settings(concurrency=1), semaphore=sem).drain()
    assert job_types.heavy_order() == [USER_A, USER_B, USER_A, USER_A, USER_A, USER_A]


async def test_round_robin_three_users(
    jobs_db: PgDatabase, job_types: Recorder, valkey: Valkey
) -> None:
    await _enqueue(USER_A, 3)
    await _enqueue(USER_B, 3)
    await _enqueue(USER_C, 1)
    sem = GpuSemaphore(valkey, limit=1, ttl_s=30)
    await Runner(make_settings(concurrency=1), semaphore=sem).drain()
    assert job_types.heavy_order() == [
        USER_A,
        USER_B,
        USER_C,
        USER_A,
        USER_B,
        USER_A,
        USER_B,
    ]


async def test_priority_comes_before_fairness(
    jobs_db: PgDatabase, job_types: Recorder, valkey: Valkey
) -> None:
    await _enqueue(USER_A, 1)
    await queue.enqueue("heavy_sleep", {"ms": 0}, user_id=USER_A, priority=10)
    await _enqueue(USER_B, 1)
    sem = GpuSemaphore(valkey, limit=1, ttl_s=30)
    await Runner(make_settings(concurrency=1), semaphore=sem).drain()
    assert job_types.heavy_order() == [USER_A, USER_B, USER_A]


async def test_never_more_than_n_heavy_jobs(
    jobs_db: PgDatabase, job_types: Recorder, valkey: Valkey
) -> None:
    await _enqueue(USER_A, 6, ms=100)
    await _enqueue(USER_B, 6, ms=100)
    for _ in range(4):
        await queue.enqueue("noop", {})
    settings = make_settings(concurrency=4)
    runners = [
        Runner(settings, semaphore=GpuSemaphore(valkey, limit=2, ttl_s=30)) for _ in range(3)
    ]
    await asyncio.gather(*(r.drain() for r in runners))
    assert job_types.peak_heavy == 2
    assert {j.status for j in await all_jobs()} == {JobStatus.SUCCEEDED}
    assert await GpuSemaphore(valkey, limit=2, ttl_s=30).held() == 0


async def test_valkey_down_pauses_heavy_jobs_only(jobs_db: PgDatabase, job_types: Recorder) -> None:
    await _enqueue(USER_A, 2)
    for _ in range(3):
        await queue.enqueue("noop", {})
    client = Valkey.from_url("valkey://127.0.0.1:1/0", socket_connect_timeout=0.5)
    sem = GpuSemaphore(client, limit=2, ttl_s=30)
    service.register_checks(sem)
    try:
        await Runner(make_settings(), semaphore=sem).drain()
        statuses = {j.type: j.status for j in await all_jobs()}
        assert statuses == {"noop": JobStatus.SUCCEEDED, "heavy_sleep": JobStatus.QUEUED}
        assert await readiness.failing_checks() == [service.CHECK_VALKEY]
    finally:
        service.unregister_checks()
        await client.aclose()
