"""AC #3: claiming, retries with backoff, leases and expiry."""

import asyncio
import uuid
from collections import Counter
from datetime import timedelta

import pytest
from sqlalchemy import text, update

from app.core.db import transaction
from app.core.timeutil import utcnow
from app.jobs import queue
from app.jobs.models import Job, JobStatus
from app.jobs.queue import Runner, backoff_delay
from tests.conftest import PgDatabase
from tests.jobs.conftest import (
    SENSITIVE,
    FakeClock,
    LogCapture,
    Recorder,
    all_jobs,
    fetch_job,
    job_rows_as_text,
    make_settings,
)


def test_backoff_is_exponential_and_capped() -> None:
    delays = [backoff_delay(n, base_s=10, max_s=60).total_seconds() for n in range(1, 6)]
    assert delays == [10, 20, 40, 60, 60]


async def test_claim_order_priority_then_run_after(jobs_db: PgDatabase, clock: FakeClock) -> None:
    late = await queue.enqueue("noop", {"n": 1}, priority=100)
    urgent = await queue.enqueue("noop", {"n": 2}, priority=10)
    later = await queue.enqueue("noop", {"n": 3}, priority=100)
    future = await queue.enqueue(
        "noop", {"n": 4}, priority=1, run_after=clock.now + timedelta(hours=1)
    )
    clock.advance(1)
    order = []
    while (job := await queue.claim(["noop"], now=clock.now, lease_s=120)) is not None:
        order.append(job.id)
    assert order == [urgent, late, later]
    claimed = await fetch_job(urgent)
    assert claimed.status == JobStatus.RUNNING
    assert claimed.attempts == 1
    assert claimed.lease_until == clock.now + timedelta(seconds=120)
    assert (await fetch_job(future)).status == JobStatus.QUEUED


async def test_concurrent_claims_never_share_a_job(jobs_db: PgDatabase) -> None:
    ids = {await queue.enqueue("noop", {"n": i}) for i in range(20)}
    now = utcnow() + timedelta(seconds=1)
    results = await asyncio.gather(
        *(queue.claim(["noop"], now=now, lease_s=120) for _ in range(30))
    )
    claimed = [r.id for r in results if r is not None]
    assert len(claimed) == len(set(claimed)) == 20
    assert set(claimed) == ids


async def test_two_runners_run_each_job_once(jobs_db: PgDatabase, job_types: Recorder) -> None:
    ids = {await queue.enqueue("noop", {"n": i}) for i in range(30)}
    settings = make_settings(concurrency=3)
    runners = [Runner(settings, semaphore=None) for _ in range(2)]
    await asyncio.gather(*(r.drain() for r in runners))
    counts = Counter(job_id for _, job_id, _ in job_types.calls)
    assert set(counts) == ids
    assert set(counts.values()) == {1}
    assert {j.status for j in await all_jobs()} == {JobStatus.SUCCEEDED}


async def test_failing_handler_retries_with_backoff_then_succeeds(
    jobs_db: PgDatabase, job_types: Recorder, clock: FakeClock
) -> None:
    job_id = await queue.enqueue("fail_twice", {})
    clock.advance(1)
    runner = Runner(make_settings(), semaphore=None, clock=clock)

    await runner.drain()
    job = await fetch_job(job_id)
    assert (job.status, job.attempts, job.error_code) == ("queued", 1, "unhandled_exception")
    assert job.run_after == clock.now + timedelta(seconds=10)

    clock.advance(9)
    await runner.drain()
    assert (await fetch_job(job_id)).attempts == 1  # not due yet

    clock.advance(1)
    await runner.drain()
    job = await fetch_job(job_id)
    assert (job.status, job.attempts) == ("queued", 2)
    assert job.run_after == clock.now + timedelta(seconds=20)

    clock.advance(20)
    await runner.drain()
    job = await fetch_job(job_id)
    assert (job.status, job.attempts, job.error_code) == ("succeeded", 3, None)
    assert job.lease_until is None
    assert job.finished_at == clock.now


async def test_out_of_attempts_fails_with_code_only(
    jobs_db: PgDatabase, job_types: Recorder, clock: FakeClock, logs: LogCapture
) -> None:
    job_id = await queue.enqueue("always_fail", {})
    runner = Runner(make_settings(), semaphore=None, clock=clock)
    for _ in range(3):
        clock.advance(1000)
        await runner.drain()
    job = await fetch_job(job_id)
    assert (job.status, job.attempts, job.error_code) == ("failed", 3, "unhandled_exception")
    assert len(job_types.calls) == 3
    # The exception text is in neither the DB nor the logs; its type and location are.
    assert SENSITIVE not in await job_rows_as_text()
    assert SENSITIVE not in logs.text
    assert "ABCDE1234F" not in logs.text
    failed = [e for e in logs.events if e["event"] == "job.failed"]
    assert failed
    assert failed[0]["error_code"] == "unhandled_exception"
    assert failed[0]["exc_type"] == "ValueError"
    assert failed[0]["module"] == "jobs.always_fail"
    assert [e["event"] for e in logs.events].count("job.retry_scheduled") == 2


@pytest.mark.parametrize(
    ("payload", "status", "attempts", "code"),
    [
        ({"code": "parser_timeout"}, "queued", 1, "parser_timeout"),
        ({"code": "file_corrupt", "mode": "permanent"}, "failed", 1, "file_corrupt"),
        ({"code": "Has_Upper"}, "queued", 1, "job_error"),
    ],
)
async def test_job_error_codes(  # noqa: PLR0917 (fixtures plus parametrize)
    jobs_db: PgDatabase,
    job_types: Recorder,
    clock: FakeClock,
    payload: dict[str, str],
    status: str,
    attempts: int,
    code: str,
) -> None:
    job_id = await queue.enqueue("coded_fail", payload)
    clock.advance(1)
    await Runner(make_settings(), semaphore=None, clock=clock).drain()
    job = await fetch_job(job_id)
    assert (job.status, job.attempts, job.error_code) == (status, attempts, code)


async def test_invalid_stored_payload_fails_permanently(
    jobs_db: PgDatabase, job_types: Recorder, clock: FakeClock
) -> None:
    job_id = await queue.enqueue("noop", {"n": 1})
    async with transaction() as tx:
        await tx.execute(update(Job).where(Job.id == job_id).values(payload={"n": "x"}))
    clock.advance(1)
    await Runner(make_settings(), semaphore=None, clock=clock).drain()
    job = await fetch_job(job_id)
    assert (job.status, job.error_code) == ("failed", "invalid_payload")
    assert job_types.calls == []


async def test_expired_lease_is_requeued_and_rerun(
    jobs_db: PgDatabase, job_types: Recorder, clock: FakeClock
) -> None:
    job_id = await queue.enqueue("noop", {})
    clock.advance(1)
    crashed = await queue.claim(["noop"], now=clock.now, lease_s=120)
    assert crashed is not None
    runner = Runner(make_settings(), semaphore=None, clock=clock)

    clock.advance(119)
    assert await runner.reap() == 0
    clock.advance(2)
    assert await runner.reap() == 1
    job = await fetch_job(job_id)
    assert (job.status, job.attempts, job.error_code) == ("queued", 1, "lease_expired")
    assert job.lease_until is None

    # The crashed runner can no longer touch it.
    assert not await queue.complete(job_id, crashed.lease_token, now=clock.now)
    await runner.drain()
    job = await fetch_job(job_id)
    assert (job.status, job.attempts) == ("succeeded", 2)


async def test_expired_lease_on_last_attempt_fails(jobs_db: PgDatabase, clock: FakeClock) -> None:
    job_id = await queue.enqueue("noop", {}, max_attempts=1)
    clock.advance(1)
    assert await queue.claim(["noop"], now=clock.now, lease_s=10) is not None
    clock.advance(11)
    assert await queue.requeue_expired(now=clock.now) == 1
    job = await fetch_job(job_id)
    assert (job.status, job.error_code) == ("failed", "lease_expired")


async def test_runner_reaps_on_startup(
    jobs_db: PgDatabase, job_types: Recorder, clock: FakeClock
) -> None:
    job_id = await queue.enqueue("noop", {})
    clock.advance(1)
    assert await queue.claim(["noop"], now=clock.now, lease_s=10) is not None
    clock.advance(60)
    stop = asyncio.Event()
    runner = Runner(make_settings(), semaphore=None, clock=clock)
    task = asyncio.create_task(runner.run(stop))
    for _ in range(100):
        if (await fetch_job(job_id)).status == JobStatus.SUCCEEDED:
            break
        await asyncio.sleep(0.02)
    stop.set()
    await task
    assert (await fetch_job(job_id)).status == JobStatus.SUCCEEDED


async def test_heartbeat_renews_the_lease(jobs_db: PgDatabase, job_types: Recorder) -> None:
    job_id = await queue.enqueue("heavy_sleep", {"ms": 900})
    settings = make_settings(lease_s=0.4, heartbeat_s=0.1)
    runner = Runner(settings, semaphore=None, job_types=None)
    # Run the heavy type as light (no semaphore) by claiming through the runner directly.
    claimed = await queue.claim(["heavy_sleep"], now=utcnow(), lease_s=settings.lease_s)
    assert claimed is not None
    task = asyncio.create_task(runner._execute(claimed, None))
    await asyncio.sleep(0.7)
    # Without heartbeats the 0.4 s lease would have expired by now.
    assert await queue.requeue_expired(now=utcnow()) == 0
    await task
    assert (await fetch_job(job_id)).status == JobStatus.SUCCEEDED


async def test_lost_lease_cancels_the_handler(jobs_db: PgDatabase, job_types: Recorder) -> None:
    job_id = await queue.enqueue("heavy_sleep", {"ms": 5000})
    settings = make_settings(heartbeat_s=0.05)
    runner = Runner(settings, semaphore=None)
    claimed = await queue.claim(["heavy_sleep"], now=utcnow(), lease_s=120)
    assert claimed is not None
    task = asyncio.create_task(runner._execute(claimed, None))
    await job_types.heavy_started.wait()
    other_token = uuid.uuid4()
    async with transaction() as tx:  # another worker re-claimed it
        await tx.execute(update(Job).where(Job.id == job_id).values(lease_token=other_token))
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)
    assert job_types.cancelled == [job_id]
    job = await fetch_job(job_id)
    # The other worker's claim is untouched.
    assert (job.status, job.lease_token) == ("running", other_token)


async def test_stale_token_writes_are_noops(jobs_db: PgDatabase, clock: FakeClock) -> None:
    job_id = await queue.enqueue("noop", {})
    clock.advance(1)
    claimed = await queue.claim(["noop"], now=clock.now, lease_s=120)
    assert claimed is not None
    stale = uuid.uuid4()
    settings = make_settings()
    assert not await queue.heartbeat(job_id, stale, now=clock.now, lease_s=120)
    assert not await queue.complete(job_id, stale, now=clock.now)
    assert not await queue.release(job_id, stale, now=clock.now)
    assert (
        await queue.fail(
            job_id, stale, now=clock.now, error_code="x", retry=True, settings=settings
        )
        is None
    )
    assert (await fetch_job(job_id)).status == JobStatus.RUNNING


async def test_enqueue_inside_a_caller_transaction(
    jobs_db: PgDatabase, job_types: Recorder
) -> None:
    async def enqueue_then_fail() -> None:
        async with transaction() as tx:
            await queue.enqueue("noop", {}, session=tx)
            raise RuntimeError("rolled back")

    with pytest.raises(RuntimeError):
        await enqueue_then_fail()
    async with transaction() as tx:
        assert (await tx.execute(text("SELECT count(*) FROM jobs"))).scalar() == 0
        job_id = await queue.enqueue("noop", {}, session=tx)
    assert (await fetch_job(job_id)).status == JobStatus.QUEUED
