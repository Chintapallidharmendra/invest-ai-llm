"""AC #5 / #6: the worker process: health endpoint, SIGTERM and graceful shutdown."""

import asyncio
import os
import signal
import socket
from pathlib import Path
from typing import Any

import httpx
import pytest

import app
from app.jobs import queue
from app.jobs.models import JobStatus
from app.jobs.settings import JobsSettings
from app.llm.gateway import close_gateway
from app.worker.main import create_health_app, discover_job_modules, run_worker
from tests.conftest import PgDatabase
from tests.crypto.conftest import kek_bytes
from tests.fakes.fake_llm import FakeLLM
from tests.jobs.conftest import USER_A, Recorder, all_jobs, fetch_job, make_settings

__all__ = ["kek_bytes"]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_healthy(port: int) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        for _ in range(200):
            try:
                return await client.get(f"http://127.0.0.1:{port}/healthz")
            except httpx.TransportError:
                await asyncio.sleep(0.02)
    pytest.fail("worker health endpoint never came up")


def test_health_defaults_to_internal_8081() -> None:
    settings = JobsSettings()
    assert (settings.health_host, settings.health_port) == ("127.0.0.1", 8081)
    assert settings.heavy_concurrency == 2
    assert settings.lease_s == 120
    assert settings.timezone == "Asia/Kolkata"


async def test_health_app_has_no_api_routes() -> None:
    transport = httpx.ASGITransport(app=create_health_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://worker") as client:
        assert (await client.get("/healthz")).status_code == 200
        assert (await client.get("/metrics")).status_code == 200
        for path in ("/api/v1/openapi.json", "/openapi.json", "/docs", "/api/v1/jobs"):
            assert (await client.get(path)).status_code == 404


def test_discovery_imports_module_jobs_files() -> None:
    # Exactly the app/<module>/jobs.py files that exist, and nothing else.
    expected = sorted(
        f"app.{path.parent.name}.jobs" for path in Path(app.__file__).parent.glob("*/jobs.py")
    )
    assert discover_job_modules() == expected
    assert "app.audit.jobs" in expected  # Story 7.1


async def test_worker_serves_health_and_runs_jobs(
    jobs_db: PgDatabase,
    job_types: Recorder,
    valkey_url: str,
    kek_bytes: bytes,
    fake_llm_env: FakeLLM,
) -> None:
    # The worker's readiness includes the KEK and both LLM servers (Story 2.2).
    await close_gateway()
    port = _free_port()
    stop = asyncio.Event()
    settings = make_settings(valkey_url, health_port=port)
    task = asyncio.create_task(run_worker(settings, stop=stop, install_signal_handlers=False))
    try:
        response = await _wait_healthy(port)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        async with httpx.AsyncClient() as client:
            ready = await client.get(f"http://127.0.0.1:{port}/readyz")
        assert ready.status_code == 200, ready.text

        job_id = await queue.enqueue("noop", {})
        heavy_id = await queue.enqueue("heavy_sleep", {"ms": 10}, user_id=USER_A)
        for _ in range(200):
            statuses = {(await fetch_job(j)).status for j in (job_id, heavy_id)}
            if statuses == {JobStatus.SUCCEEDED}:
                break
            await asyncio.sleep(0.02)
        assert statuses == {JobStatus.SUCCEEDED}
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=10)
        await close_gateway()


async def _run_until_sigterm(settings: JobsSettings, recorder: Recorder) -> None:
    task = asyncio.create_task(run_worker(settings))
    await _wait_healthy(settings.health_port)
    await asyncio.wait_for(recorder.heavy_started.wait(), timeout=5)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, timeout=10)


async def _no_job_left_running() -> list[Any]:
    jobs = await all_jobs()
    assert all(j.status != JobStatus.RUNNING for j in jobs)
    assert all(j.lease_until is None and j.lease_token is None for j in jobs)
    return jobs


async def test_sigterm_lets_short_jobs_finish(
    jobs_db: PgDatabase, job_types: Recorder, valkey_url: str
) -> None:
    job_id = await queue.enqueue("heavy_sleep", {"ms": 300}, user_id=USER_A)
    settings = make_settings(valkey_url, health_port=_free_port(), shutdown_grace_s=5)
    await _run_until_sigterm(settings, job_types)
    await _no_job_left_running()
    assert (await fetch_job(job_id)).status == JobStatus.SUCCEEDED


async def test_sigterm_releases_long_jobs(
    jobs_db: PgDatabase, job_types: Recorder, valkey_url: str
) -> None:
    job_id = await queue.enqueue("heavy_sleep", {"ms": 60_000}, user_id=USER_A)
    settings = make_settings(valkey_url, health_port=_free_port(), shutdown_grace_s=0.2)
    await _run_until_sigterm(settings, job_types)
    await _no_job_left_running()
    job = await fetch_job(job_id)
    # Back in the queue, and the interrupted attempt doesn't count.
    assert (job.status, job.attempts) == (JobStatus.QUEUED, 0)
    assert job_types.cancelled == [job_id]
