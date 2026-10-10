"""AC #1: migration ``7_2_outputs`` round trip and its RLS setup."""

import pytest

from app.spaces.rls import policy_name
from tests.conftest import PgDatabase
from tests.spaces.conftest import as_migrator
from tests.spaces.test_rls import _alembic

pytestmark = pytest.mark.db

TABLES = ("outputs", "download_tokens")


async def test_tables_have_forced_owner_only_rls(pg: PgDatabase) -> None:
    rows = await as_migrator(
        pg.migrator_url,
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relname = ANY(:t) ORDER BY relname",
        {"t": list(TABLES)},
    )
    assert rows == [(t, True, True) for t in sorted(TABLES)]
    policies = dict(
        await as_migrator(
            pg.migrator_url,
            "SELECT tablename, qual FROM pg_policies WHERE policyname = ANY(:p)",
            {"p": [policy_name(t) for t in TABLES]},
        )
    )
    assert "owner_user_id = ( SELECT app_user_id()" in policies["outputs"]
    assert "user_id = ( SELECT app_user_id()" in policies["download_tokens"]


async def test_migration_down_and_up(pg: PgDatabase) -> None:
    query = "SELECT count(*) FROM pg_tables WHERE tablename = ANY(:t)"
    _alembic(pg, "downgrade", "8_2_spaces")
    assert await as_migrator(pg.migrator_url, query, {"t": list(TABLES)}) == [(0,)]
    _alembic(pg, "upgrade", "head")
    assert await as_migrator(pg.migrator_url, query, {"t": list(TABLES)}) == [(2,)]
