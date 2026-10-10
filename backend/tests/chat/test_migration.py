"""AC #1: migration ``3_1_conversations`` round trip and its RLS setup."""

import pytest

from app.spaces.rls import policy_name
from tests.conftest import PgDatabase
from tests.spaces.conftest import as_migrator
from tests.spaces.test_rls import _alembic

pytestmark = pytest.mark.db

TABLES = (
    "conversations",
    "conversation_documents",
    "messages",
    "message_tables",
    "message_sources",
)


async def test_tables_have_forced_owner_only_rls(pg: PgDatabase) -> None:
    rows = await as_migrator(
        pg.migrator_url,
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relname = ANY(:t) ORDER BY relname",
        {"t": list(TABLES)},
    )
    assert rows == [(t, True, True) for t in sorted(TABLES)]
    policies = await as_migrator(
        pg.migrator_url,
        "SELECT tablename, policyname, qual FROM pg_policies WHERE tablename = ANY(:t)",
        {"t": list(TABLES)},
    )
    assert {(t, p) for t, p, _ in policies} == {(t, policy_name(t)) for t in TABLES}
    assert all("owner_user_id = ( SELECT app_user_id()" in q for _, _, q in policies)


async def test_no_plaintext_content_columns(pg: PgDatabase) -> None:
    rows = await as_migrator(
        pg.migrator_url,
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_name = ANY(:t) AND data_type = 'text'",
        {"t": list(TABLES)},
    )
    # Text columns are metadata only: model/prompt names, codes and locators.
    assert set(rows) == {
        ("messages", "decline_category"),
        ("messages", "model_name"),
        ("messages", "prompt_version"),
        ("message_sources", "kind"),
        ("message_sources", "locator"),
    }


async def test_migration_down_and_up(pg: PgDatabase) -> None:
    _alembic(pg, "downgrade", "8_2_spaces")
    count = await as_migrator(
        pg.migrator_url,
        "SELECT count(*) FROM pg_tables WHERE tablename = ANY(:t)",
        {"t": list(TABLES)},
    )
    assert count == [(0,)]
    _alembic(pg, "upgrade", "head")
    count = await as_migrator(
        pg.migrator_url,
        "SELECT count(*) FROM pg_tables WHERE tablename = ANY(:t)",
        {"t": list(TABLES)},
    )
    assert count == [(len(TABLES),)]
