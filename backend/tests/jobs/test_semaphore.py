"""AC #4: the Valkey GPU semaphore."""

import asyncio

import pytest
from valkey.asyncio import Valkey
from valkey.exceptions import ConnectionError as ValkeyConnectionError

from app.jobs.semaphore import GpuSemaphore


async def test_limit_release_and_reacquire(valkey: Valkey) -> None:
    sem = GpuSemaphore(valkey, limit=2, ttl_s=30)
    a, b, c = (sem.new_holder() for _ in range(3))
    assert await sem.acquire(a)
    assert await sem.acquire(b)
    assert not await sem.acquire(c)
    assert await sem.acquire(a)  # re-acquiring a held slot doesn't take another
    assert await sem.held() == 2
    await sem.release(a)
    assert await sem.acquire(c)
    assert await sem.held() == 2


async def test_crashed_holders_expire(valkey: Valkey) -> None:
    sem = GpuSemaphore(valkey, limit=1, ttl_s=0.2)
    assert await sem.acquire("crashed")
    assert not await sem.acquire("waiting")
    await asyncio.sleep(0.3)
    assert await sem.acquire("waiting")
    # The expired holder can't renew its way back in.
    assert not await sem.renew("crashed")


async def test_renew_keeps_a_live_holder(valkey: Valkey) -> None:
    sem = GpuSemaphore(valkey, limit=1, ttl_s=0.3)
    assert await sem.acquire("live")
    for _ in range(3):
        await asyncio.sleep(0.15)
        assert await sem.renew("live")
    assert not await sem.acquire("other")


async def test_unreachable_valkey_means_no_slot() -> None:
    client = Valkey.from_url("valkey://127.0.0.1:1/0", socket_connect_timeout=0.5)
    sem = GpuSemaphore(client, limit=2, ttl_s=30)
    try:
        assert not await sem.acquire("h")
        assert not await sem.renew("h")
        await sem.release("h")  # logged, not raised
        with pytest.raises(ValkeyConnectionError):
            await sem.ping()
    finally:
        await client.aclose()


def test_limit_must_be_positive(valkey: Valkey) -> None:
    with pytest.raises(ValueError, match="limit"):
        GpuSemaphore(valkey, limit=0, ttl_s=1)
