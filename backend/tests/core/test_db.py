import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.db import NestedTransactionError, transaction

BACKEND_DIR = Path(__file__).resolve().parents[2]

CURRENT_USER_ID = text("SELECT current_setting('app.user_id', true)")


async def test_unknown_context_key_rejected() -> None:
    with pytest.raises(ValueError, match=r"app\.role"):
        async with transaction({"app.role": "admin"}):
            pass


@pytest.mark.db
async def test_context_is_local_to_transaction(database: str) -> None:
    async with transaction({"app.user_id": "u-123", "app.grant_id": "g-9"}) as session:
        assert (await session.execute(CURRENT_USER_ID)).scalar() == "u-123"
        grant = await session.execute(text("SELECT current_setting('app.grant_id', true)"))
        assert grant.scalar() == "g-9"

    # Pooled connections reuse sessions; the local setting must not survive the commit.
    for _ in range(3):
        async with transaction() as session:
            assert (await session.execute(CURRENT_USER_ID)).scalar() in (None, "")


@pytest.mark.db
async def test_no_context_sets_nothing(database: str) -> None:
    async with transaction(None) as session:
        assert (await session.execute(CURRENT_USER_ID)).scalar() in (None, "")


@pytest.mark.db
async def test_rollback_on_error(database: str) -> None:
    async def create_then_fail() -> None:
        async with transaction({"app.user_id": "u-1"}) as session:
            await session.execute(text("CREATE TEMP TABLE t_rollback (id int)"))
            raise RuntimeError("abort")

    with pytest.raises(RuntimeError):
        await create_then_fail()
    async with transaction() as session:
        exists = await session.execute(text("SELECT to_regclass('pg_temp.t_rollback')"))
        assert exists.scalar() is None


@pytest.mark.db
async def test_nested_transaction_rejected(database: str) -> None:
    async with transaction():
        with pytest.raises(NestedTransactionError):
            async with transaction():
                pass


@pytest.mark.db
async def test_alembic_upgrade_creates_extensions(test_database_url: str) -> None:
    env = {"APP_MIGRATOR_DATABASE_URL": test_database_url, "PATH": "/usr/bin:/bin"}
    for args in (["downgrade", "base"], ["upgrade", "head"]):
        await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND_DIR,
            env=env,
            check=True,
            capture_output=True,
        )

    engine = create_async_engine(test_database_url)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname IN ('vector', 'citext')")
            )
            assert sorted(r[0] for r in rows) == ["citext", "vector"]
            version = await conn.execute(text("SELECT version_num FROM alembic_version"))
            assert version.scalar() == "1_2_baseline"
    finally:
        await engine.dispose()
