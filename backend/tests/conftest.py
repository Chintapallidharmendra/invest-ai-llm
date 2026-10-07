"""Shared, generic fixtures (Story 1.6; ADR-037). Per-module fixtures go in
``tests/<module>/conftest.py``.

- ``pg``: PostgreSQL 18 + pgvector from ``deploy/compose.test.yml``, migrated to head as
  ``app_migrator``. The app's own sessions (``app.core.db``) connect as **``app_rw``**,
  so RLS applies. Tables are truncated after each test, as ``app_migrator``.
- ``valkey``: an async Valkey client; the database is flushed after each test.
- ``fake_llm``: the in-process fake LLM (:class:`tests.fakes.fake_llm.FakeLLM`), reset
  for each test. Use ``fake_llm.add(...)`` and ``fake_llm.requests``.
- ``app_client``: an httpx ``AsyncClient`` against ``create_app()`` with the LLM
  settings pointing at ``fake_llm``.

Postgres and Valkey are found via ``docker compose -f deploy/compose.test.yml port``
(start them with ``docker compose -f deploy/compose.test.yml up -d --wait``), or via
``APP_TEST_PG_ADDR`` / ``APP_TEST_VALKEY_ADDR`` (``host:port``) in CI. Tests that need
them are skipped when neither is available.
"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from valkey.asyncio import Valkey

from app.api.app import create_app
from app.core import db
from app.core.config import get_settings
from tests.fakes.fake_llm import FakeLLM

BACKEND_DIR: Final = Path(__file__).resolve().parents[1]
COMPOSE_TEST_FILE: Final = BACKEND_DIR.parent / "deploy" / "compose.test.yml"

# Throwaway credentials of the loopback-only test database (deploy/compose.test.yml).
PG_DB: Final = "invest_ai"
PG_APP_RW: Final = ("app_rw", "test-app-rw")
PG_MIGRATOR: Final = ("app_migrator", "test-migrator")

# Settings that point the app at the fake LLM (read by app.llm.settings, Story 1.5).
LLM_SALT_SECRET: Final = "test-cache-salt-secret-not-for-production"  # noqa: S105


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "db: needs the test PostgreSQL (deploy/compose.test.yml)")


def _compose_port(service: str, port: int) -> str | None:
    if shutil.which("docker") is None or not COMPOSE_TEST_FILE.is_file():
        return None
    try:
        result = subprocess.run(  # noqa: S603 (fixed argv, no shell)
            ["docker", "compose", "-f", str(COMPOSE_TEST_FILE), "port", service, str(port)],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    address = result.stdout.strip().splitlines()[-1:] if result.returncode == 0 else []
    return address[0] if address and not address[0].endswith(":0") else None


def _service_address(env: str, service: str, port: int) -> str:
    address = os.environ.get(env) or _compose_port(service, port)
    if not address:
        pytest.skip(
            f"{service} not available: run `docker compose -f deploy/compose.test.yml up -d "
            f"--wait` or set {env}=host:port"
        )
    return address


# --- Postgres ------------------------------------------------------------------


@dataclass(frozen=True)
class PgDatabase:
    app_url: str  # app_rw: what the app uses; subject to RLS
    migrator_url: str  # app_migrator: DDL, truncation, test setup


def _pg_url(address: str, role: tuple[str, str]) -> str:
    user, password = role
    return f"postgresql+asyncpg://{user}:{password}@{address}/{PG_DB}"


@pytest.fixture(scope="session")
def _pg_database() -> PgDatabase:
    address = _service_address("APP_TEST_PG_ADDR", "postgres", 5432)
    database = PgDatabase(_pg_url(address, PG_APP_RW), _pg_url(address, PG_MIGRATOR))
    migrate = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env={**os.environ, "APP_MIGRATOR_DATABASE_URL": database.migrator_url},
        capture_output=True,
        text=True,
        check=False,
    )
    if migrate.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\n{migrate.stderr[-2000:]}")
    return database


async def truncate_all(migrator_url: str) -> None:
    """Empty every table in ``public`` except Alembic's, as ``app_migrator``."""
    engine = create_async_engine(migrator_url)
    try:
        async with engine.begin() as conn:
            rows = await conn.execute(
                text(
                    "SELECT quote_ident(tablename) FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            )
            tables = [r[0] for r in rows]
            if tables:
                await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture
async def pg(
    _pg_database: PgDatabase, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[PgDatabase]:
    """The migrated test database; ``app.core.db`` connects to it as ``app_rw``."""
    monkeypatch.setenv("APP_DATABASE_URL", _pg_database.app_url)
    monkeypatch.setenv("APP_MIGRATOR_DATABASE_URL", _pg_database.migrator_url)
    get_settings.cache_clear()
    await db.dispose_engine()
    try:
        yield _pg_database
    finally:
        await db.dispose_engine()
        get_settings.cache_clear()
        await truncate_all(_pg_database.migrator_url)


# --- Valkey ----------------------------------------------------------------------


@pytest.fixture(scope="session")
def _valkey_url() -> str:
    return f"valkey://{_service_address('APP_TEST_VALKEY_ADDR', 'valkey', 6379)}/0"


@pytest.fixture
async def valkey(_valkey_url: str) -> AsyncIterator[Valkey]:
    client = Valkey.from_url(_valkey_url)
    try:
        await client.ping()
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


# --- Fake LLM ----------------------------------------------------------------------


@pytest.fixture(scope="session")
def _fake_llm_server() -> Iterator[FakeLLM]:
    server = FakeLLM().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def fake_llm(_fake_llm_server: FakeLLM) -> Iterator[FakeLLM]:
    _fake_llm_server.reset()
    yield _fake_llm_server
    _fake_llm_server.reset()


@pytest.fixture
def fake_llm_env(fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    """Point the app's LLM settings (``APP_LLM_*``) at the fake for chat and guard."""
    monkeypatch.setenv(
        "APP_LLM_BASE_URLS", json.dumps({"chat": fake_llm.base_url, "guard": fake_llm.base_url})
    )
    monkeypatch.setenv("APP_LLM_CACHE_SALT_SECRET", LLM_SALT_SECRET)
    get_settings.cache_clear()
    return fake_llm


# --- App -----------------------------------------------------------------------------


@pytest.fixture
async def app_client(fake_llm_env: FakeLLM) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(configure_logs=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    get_settings.cache_clear()
