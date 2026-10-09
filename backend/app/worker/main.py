"""Worker process: job runner + cron scheduler + internal health endpoint (ADR-017).

    python -m app.worker

- Imports every ``app/<module>/jobs.py`` so job types and crons register themselves.
- Runs the :class:`~app.jobs.queue.Runner` and the APScheduler ``AsyncIOScheduler``
  (``Asia/Kolkata``) on one event loop.
- Serves ``/healthz``, ``/readyz`` and ``/metrics`` on ``APP_JOBS_HEALTH_HOST:PORT``
  (default ``127.0.0.1:8081``; never published). There is no other HTTP API.
- On SIGTERM/SIGINT: stops the scheduler and claiming; in-flight jobs get
  ``shutdown_grace_s`` to finish, then their leases are released.
"""

import asyncio
import contextlib
import importlib
import importlib.util
import pkgutil
import signal
from collections.abc import Iterator

import uvicorn
from fastapi import FastAPI
from valkey.asyncio import Valkey

import app
from app.api import health
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.obs import configure_logging, get_logger
from app.crypto import kek
from app.jobs import service
from app.jobs.queue import Runner
from app.jobs.scheduler import build_scheduler
from app.jobs.semaphore import GpuSemaphore
from app.jobs.settings import JobsSettings, get_jobs_settings
from app.llm import readiness as llm_readiness

_log = get_logger("worker")


def discover_job_modules() -> list[str]:
    """Import every ``app/<module>/jobs.py``; returns the module names imported."""
    imported = []
    for info in sorted(pkgutil.iter_modules(app.__path__), key=lambda i: i.name):
        name = f"app.{info.name}.jobs"
        if info.ispkg and importlib.util.find_spec(name) is not None:
            importlib.import_module(name)
            imported.append(name)
    return imported


def create_health_app() -> FastAPI:
    health_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    health_app.include_router(health.router)
    return health_app


class _HealthServer(uvicorn.Server):
    """uvicorn without its own signal handling: the worker owns SIGTERM."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


async def run_worker(
    settings: JobsSettings | None = None,
    *,
    stop: asyncio.Event | None = None,
    install_signal_handlers: bool = True,
) -> None:
    settings = settings or get_jobs_settings()
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGTERM, signal.SIGINT)
    if install_signal_handlers:
        for sig in signals:
            loop.add_signal_handler(sig, stop.set)

    discover_job_modules()
    valkey = Valkey.from_url(
        settings.valkey_url.get_secret_value(), socket_timeout=2, socket_connect_timeout=2
    )
    semaphore = GpuSemaphore(
        valkey, limit=settings.heavy_concurrency, ttl_s=settings.semaphore_ttl_s
    )
    service.register_checks(semaphore)
    kek.register_checks()
    llm_readiness.register_checks()
    runner = Runner(settings, semaphore=semaphore)
    scheduler = build_scheduler(settings.timezone)
    server = _HealthServer(
        uvicorn.Config(
            create_health_app(),
            host=settings.health_host,
            port=settings.health_port,
            log_config=None,
            access_log=False,
            lifespan="off",
            server_header=False,
        )
    )
    server_task = asyncio.create_task(server.serve())
    scheduler.start()
    _log.info("worker.started")
    try:
        await runner.run(stop)
    finally:
        scheduler.shutdown(wait=False)
        server.should_exit = True
        await server_task
        service.unregister_checks()
        kek.unregister_checks()
        llm_readiness.unregister_checks()
        await valkey.aclose()
        await dispose_engine()
        if install_signal_handlers:
            for sig in signals:
                loop.remove_signal_handler(sig)
        _log.info("worker.stopped")


def main() -> None:
    configure_logging(get_settings().log_level)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
