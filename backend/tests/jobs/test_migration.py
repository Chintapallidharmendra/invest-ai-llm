"""AC #1: migration 1_11_jobs up and down."""

import os
import subprocess
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import BACKEND_DIR, PgDatabase


def _alembic(pg: PgDatabase, *args: str) -> None:
    result = subprocess.run(  # noqa: S603 (fixed argv)
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env={**os.environ, "APP_MIGRATOR_DATABASE_URL": pg.migrator_url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]


async def _scalar(pg: PgDatabase, sql: str) -> object:
    engine = create_async_engine(pg.migrator_url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql))).scalar()
    finally:
        await engine.dispose()


async def test_jobs_table_shape(pg: PgDatabase) -> None:
    columns = await _scalar(
        pg,
        "SELECT string_agg(column_name || ':' || data_type || ':' || is_nullable, ',' "
        "ORDER BY column_name) FROM information_schema.columns WHERE table_name = 'jobs'",
    )
    assert isinstance(columns, str)
    cols = {c.split(":")[0]: c.split(":", 1)[1] for c in columns.split(",")}
    assert cols["id"] == "uuid:NO"
    assert cols["type"] == "text:NO"
    assert cols["user_id"] == "uuid:YES"
    assert cols["space_id"] == "uuid:YES"
    assert cols["payload"] == "jsonb:NO"
    assert cols["priority"] == "integer:NO"
    assert cols["status"] == "USER-DEFINED:NO"
    assert cols["progress"] == "jsonb:YES"
    assert cols["attempts"] == "integer:NO"
    assert cols["max_attempts"] == "integer:NO"
    assert cols["run_after"] == "timestamp with time zone:NO"
    assert cols["lease_until"] == "timestamp with time zone:YES"
    assert cols["error_code"] == "text:YES"
    assert cols["correlation_id"] == "text:YES"
    assert cols["created_at"] == "timestamp with time zone:NO"
    assert cols["updated_at"] == "timestamp with time zone:NO"

    labels = await _scalar(
        pg,
        "SELECT string_agg(enumlabel, ',' ORDER BY enumsortorder) FROM pg_enum e "
        "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'job_status'",
    )
    assert labels == "queued,running,succeeded,failed,cancelled"
    index = await _scalar(
        pg, "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_jobs_status_priority_run_after'"
    )
    assert index is not None
    assert "(status, priority, run_after)" in str(index)
    defaults = await _scalar(
        pg,
        "SELECT string_agg(column_name || '=' || column_default, ',' ORDER BY column_name) "
        "FROM information_schema.columns WHERE table_name = 'jobs' "
        "AND column_name IN ('id', 'priority', 'max_attempts', 'status')",
    )
    assert defaults == ("id=uuidv7(),max_attempts=3,priority=100,status='queued'::job_status")


async def test_app_role_can_use_jobs_without_rls(pg: PgDatabase) -> None:
    engine = create_async_engine(pg.app_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO jobs (type) VALUES ('noop')"))
            assert (await conn.execute(text("SELECT count(*) FROM jobs"))).scalar() == 1
            rls = (
                await conn.execute(text("SELECT relrowsecurity FROM pg_class WHERE relname='jobs'"))
            ).scalar()
            assert rls is False
    finally:
        await engine.dispose()


async def test_downgrade_and_upgrade(pg: PgDatabase) -> None:
    _alembic(pg, "downgrade", "1_2_baseline")
    try:
        assert await _scalar(pg, "SELECT to_regclass('public.jobs')") is None
        assert await _scalar(pg, "SELECT count(*) FROM pg_type WHERE typname = 'job_status'") == 0
    finally:
        _alembic(pg, "upgrade", "head")
    assert await _scalar(pg, "SELECT to_regclass('public.jobs')::text") == "jobs"
