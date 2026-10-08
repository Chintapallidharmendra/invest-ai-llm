"""AC #5: cron registration and firing (with a fake clock)."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

import apscheduler.schedulers.base as scheduler_base  # type: ignore[import-untyped]
import pytest

from app.jobs import scheduler
from tests.jobs.conftest import SENSITIVE, LogCapture

IST = ZoneInfo("Asia/Kolkata")


class FakeDatetime(datetime):
    """Stands in for ``datetime`` inside APScheduler's scheduler loop."""

    current: datetime = datetime.now(UTC)

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:  # type: ignore[override]
        return cls.current.astimezone(tz) if tz is not None else cls.current


@pytest.fixture
def fake_time(monkeypatch: pytest.MonkeyPatch) -> type[FakeDatetime]:
    monkeypatch.setattr(scheduler_base, "datetime", FakeDatetime)
    return FakeDatetime


@pytest.fixture(autouse=True)
def _cleanup() -> Iterator[None]:
    yield
    for name in list(scheduler.registered_crons()):
        scheduler.unregister_cron(name)


def _tomorrow_ist(hour: int, minute: int, second: int = 0) -> datetime:
    # In the future, so APScheduler's executor (real clock) never sees a misfire.
    day = datetime.now(IST).date() + timedelta(days=1)
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=IST)


def test_register_validates_expression_and_name() -> None:
    async def noop() -> None:
        return None

    with pytest.raises(ValueError, match="Wrong number of fields"):
        scheduler.register_cron("bad", "not a cron", noop)
    scheduler.register_cron("nightly", "30 2 * * *", noop)
    with pytest.raises(ValueError, match="already registered"):
        scheduler.register_cron("nightly", "0 3 * * *", noop)


async def test_cron_fires_at_the_ist_time(fake_time: type[FakeDatetime]) -> None:
    fired: list[datetime] = []
    done = asyncio.Event()

    async def nightly() -> None:
        fired.append(fake_time.current)
        done.set()

    scheduler.register_cron("nightly", "0 2 * * *", nightly)
    fake_time.current = _tomorrow_ist(1, 59)
    sched = scheduler.build_scheduler()
    assert str(sched.timezone) == "Asia/Kolkata"
    sched.start()
    try:
        job = sched.get_job("nightly")
        assert job.next_run_time == _tomorrow_ist(2, 0)
        # 02:00 IST is 20:30 UTC the previous day.
        assert job.next_run_time.astimezone(UTC).hour == 20

        fake_time.current = _tomorrow_ist(1, 59, 59)
        sched.wakeup()
        await asyncio.sleep(0.05)
        assert not done.is_set()

        fake_time.current = _tomorrow_ist(2, 0, 1)
        sched.wakeup()
        await asyncio.wait_for(done.wait(), timeout=2)
        assert len(fired) == 1
        assert sched.get_job("nightly").next_run_time == _tomorrow_ist(2, 0) + timedelta(days=1)
    finally:
        sched.shutdown(wait=False)


async def test_failing_cron_is_logged_without_its_message(logs: LogCapture) -> None:
    async def broken() -> None:
        raise RuntimeError(SENSITIVE)

    cron = scheduler.register_cron("broken", "* * * * *", broken)
    sched = scheduler.build_scheduler(crons={"broken": cron})
    job = sched.get_job("broken")
    await job.func()  # the wrapper the scheduler runs
    assert SENSITIVE not in logs.text
    failed = [e for e in logs.events if e["event"] == "cron.failed"]
    assert failed[0]["module"] == "cron.broken"
    assert failed[0]["exc_type"] == "RuntimeError"
