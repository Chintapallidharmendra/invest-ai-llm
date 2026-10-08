"""Fixtures for the audit tests: a test HMAC key, the database, and tamper helpers.

``audit_db`` is the migrated ``pg`` database plus the key. Tampering (UPDATE as the
table owner with the append-only trigger disabled) uses ``app_migrator``; partition
DDL in the tests does too.
"""

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.audit import types as audit_types
from app.audit.partitions import list_partitions, month_of
from app.audit.settings import get_audit_settings
from app.audit.types import KeyedHash, VersionStr
from tests.audit.events import Colour, DummyEvent, Level, TinyEvent
from tests.conftest import PgDatabase

KEY = b"0123456789abcdef0123456789abcdef-test-audit-key"
CANARY = "Project Falcon: acquisition of Asha Rao's stake"


@pytest.fixture
def audit_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[bytes]:
    path = tmp_path / "audit_hmac_key"
    path.write_bytes(KEY + b"\n")
    monkeypatch.setenv("APP_AUDIT_HMAC_KEY_PATH", str(path))
    get_audit_settings.cache_clear()
    audit_types.audit_key.cache_clear()
    yield KEY
    get_audit_settings.cache_clear()
    audit_types.audit_key.cache_clear()


@pytest.fixture
async def audit_db(pg: PgDatabase, audit_key: bytes) -> AsyncIterator[PgDatabase]:
    yield pg
    await drop_old_partitions(pg.migrator_url)


async def drop_old_partitions(migrator_url: str) -> None:
    """Remove partitions the tests created for past months (keeps the real ones)."""
    engine = create_async_engine(migrator_url)
    this_month = month_of(datetime.now(UTC))
    try:
        async with engine.begin() as conn:
            for partition in await list_partitions(conn):
                if partition.month < this_month:
                    await conn.execute(text(f"DROP TABLE {partition.name}"))
            await conn.execute(text("SELECT audit_ensure_partitions(2)"))
    finally:
        await engine.dispose()


async def as_migrator(pg: PgDatabase, sql: str, params: dict[str, Any] | None = None) -> Any:
    engine = create_async_engine(pg.migrator_url)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), params or {})
            return result.all() if result.returns_rows else result.rowcount
    finally:
        await engine.dispose()


async def tamper(pg: PgDatabase, sql: str, params: dict[str, Any] | None = None) -> None:
    """Run ``sql`` against audit_events as its owner, with the append-only trigger off."""
    engine = create_async_engine(pg.migrator_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE audit_events DISABLE TRIGGER USER"))
            await conn.execute(text(sql), params or {})
            await conn.execute(text("ALTER TABLE audit_events ENABLE TRIGGER USER"))
    finally:
        await engine.dispose()


def dummy_event(**overrides: Any) -> DummyEvent:
    values: dict[str, Any] = {
        "object_id": uuid.UUID("00000000-0000-7000-8000-000000000001"),
        "colour": Colour.RED,
        "level": Level.HIGH,
        "count": 3,
        "flag": True,
        "at": datetime(2026, 10, 8, 9, 30, 15, 123456, tzinfo=UTC),
        "took": timedelta(seconds=1, microseconds=500),
        "model_version": VersionStr("2.0.1-rc1"),
        "name_hash": KeyedHash.of("Acme Industries"),
    }
    values.update(overrides)
    return DummyEvent(**values)


def tiny(n: int = 1) -> TinyEvent:
    return TinyEvent(n=n)


CORRELATION = uuid.UUID("00000000-0000-7000-8000-0000000000cc")
ACTOR = uuid.UUID("00000000-0000-7000-8000-0000000000aa")
ONE_DAY = timedelta(days=1)
