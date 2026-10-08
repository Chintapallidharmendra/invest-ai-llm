"""Fixtures for the job queue tests: dummy job types, a fake clock and settings.

Dummy types (registered per test, unregistered after):

- ``noop``: records its call.
- ``fail_twice``: raises on its first two attempts, then succeeds.
- ``always_fail``: raises an exception whose message holds sensitive text.
- ``coded_fail``: raises ``JobError``/``PermanentJobError`` with the payload's code.
- ``heavy_sleep`` (``llm_heavy``): sleeps ``ms`` and tracks peak concurrency.
"""

import asyncio
import io
import json
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import select, text
from valkey.asyncio import Valkey

from app.core import obs
from app.core.db import transaction
from app.core.timeutil import utcnow
from app.jobs import registry
from app.jobs.models import Job
from app.jobs.registry import Identifier, JobContext, JobError, PermanentJobError
from app.jobs.settings import JobsSettings
from tests.conftest import PgDatabase

SENSITIVE = "PAN ABCDE1234F of Asha Rao, revenue 4,215.5 cr"
USER_A = uuid.UUID("00000000-0000-7000-8000-00000000000a")
USER_B = uuid.UUID("00000000-0000-7000-8000-00000000000b")
USER_C = uuid.UUID("00000000-0000-7000-8000-00000000000c")


class FakeClock:
    """A controllable UTC clock; starts at the real time and only moves forward."""

    def __init__(self) -> None:
        self.now = utcnow()

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Mode(StrEnum):
    RETRY = "retry"
    PERMANENT = "permanent"


class NoopPayload(BaseModel):
    n: int = 0


class SleepPayload(BaseModel):
    ms: int = 0


class CodedPayload(BaseModel):
    code: Identifier
    mode: Mode = Mode.RETRY


@dataclass
class Recorder:
    calls: list[tuple[str, uuid.UUID, uuid.UUID | None]] = field(default_factory=list)
    attempts: dict[uuid.UUID, int] = field(default_factory=dict)
    running_heavy: int = 0
    peak_heavy: int = 0
    cancelled: list[uuid.UUID] = field(default_factory=list)
    heavy_started: asyncio.Event = field(default_factory=asyncio.Event)

    def heavy_order(self) -> list[uuid.UUID | None]:
        return [user for name, _, user in self.calls if name == "heavy_sleep"]


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def job_types(recorder: Recorder) -> Iterator[Recorder]:
    async def noop(ctx: JobContext, payload: NoopPayload) -> None:
        recorder.calls.append(("noop", ctx.job_id, ctx.user_id))

    async def fail_twice(ctx: JobContext, payload: NoopPayload) -> None:
        recorder.calls.append(("fail_twice", ctx.job_id, ctx.user_id))
        recorder.attempts[ctx.job_id] = ctx.attempt
        if ctx.attempt <= 2:
            raise RuntimeError(SENSITIVE)

    async def always_fail(ctx: JobContext, payload: NoopPayload) -> None:
        recorder.calls.append(("always_fail", ctx.job_id, ctx.user_id))
        raise ValueError(SENSITIVE)

    async def coded_fail(ctx: JobContext, payload: CodedPayload) -> None:
        recorder.calls.append(("coded_fail", ctx.job_id, ctx.user_id))
        if payload.mode is Mode.PERMANENT:
            raise PermanentJobError(payload.code)
        raise JobError(payload.code)

    async def heavy_sleep(ctx: JobContext, payload: SleepPayload) -> None:
        recorder.calls.append(("heavy_sleep", ctx.job_id, ctx.user_id))
        recorder.running_heavy += 1
        recorder.peak_heavy = max(recorder.peak_heavy, recorder.running_heavy)
        recorder.heavy_started.set()
        try:
            await ctx.progress(0, 2)
            await asyncio.sleep(payload.ms / 1000)
            await ctx.progress(2, 2)
        except asyncio.CancelledError:
            recorder.cancelled.append(ctx.job_id)
            raise
        finally:
            recorder.running_heavy -= 1

    registry.register("noop", NoopPayload, noop)
    registry.register("fail_twice", NoopPayload, fail_twice)
    registry.register("always_fail", NoopPayload, always_fail)
    registry.register("coded_fail", CodedPayload, coded_fail)
    registry.register("heavy_sleep", SleepPayload, heavy_sleep, llm_heavy=True)
    try:
        yield recorder
    finally:
        for name in ("noop", "fail_twice", "always_fail", "coded_fail", "heavy_sleep"):
            registry.unregister(name)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def make_settings(valkey_url: str = "valkey://127.0.0.1:1/0", **overrides: Any) -> JobsSettings:
    values: dict[str, Any] = {
        "valkey_url": valkey_url,
        "lease_s": 120.0,
        "heartbeat_s": 30.0,
        "poll_interval_s": 0.02,
        "reap_interval_s": 30.0,
        "backoff_base_s": 10.0,
        "backoff_max_s": 600.0,
        "concurrency": 4,
        "heavy_concurrency": 2,
        "semaphore_ttl_s": 90.0,
        "shutdown_grace_s": 1.0,
        "health_port": 0,
    }
    values.update(overrides)
    return JobsSettings(**values)


@pytest.fixture
def valkey_url(_valkey_url: str, valkey: Valkey) -> str:
    """The test Valkey's URL (flushed after the test via ``valkey``)."""
    return _valkey_url


@pytest.fixture
async def jobs_db(pg: PgDatabase, job_types: Recorder) -> PgDatabase:
    """The migrated database plus the dummy job types."""
    return pg


async def fetch_job(job_id: uuid.UUID) -> Job:
    async with transaction() as tx:
        return (await tx.execute(select(Job).where(Job.id == job_id))).scalar_one()


async def all_jobs() -> list[Job]:
    async with transaction() as tx:
        return list((await tx.execute(select(Job).order_by(Job.created_at))).scalars())


async def job_rows_as_text() -> str:
    """Every column of every job, as Postgres renders it."""
    async with transaction() as tx:
        rows = await tx.execute(text("SELECT jobs::text FROM jobs"))
        return "\n".join(r[0] for r in rows)


class LogCapture:
    def __init__(self) -> None:
        self.stream = io.StringIO()

    @property
    def text(self) -> str:
        return self.stream.getvalue()

    @property
    def events(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.text.splitlines() if line.strip()]


@pytest.fixture
def logs() -> Iterator[LogCapture]:
    capture = LogCapture()
    obs.configure_logging("DEBUG", stream=capture.stream)
    yield capture
    obs.configure_logging("INFO", stream=sys.stdout)
