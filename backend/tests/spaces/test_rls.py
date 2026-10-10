"""AC #1 / #2: the migration, constraints, and the RLS policies (as app_rw and owner)."""

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.db import transaction
from app.crypto.keys import create_space_key
from app.spaces.rls import disable_space_rls_statements, space_rls_statements
from tests.conftest import BACKEND_DIR, PgDatabase
from tests.spaces.conftest import as_migrator, as_user, close_space, make_user, make_workspace

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("spaces_db")]


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


# --- AC #1 ------------------------------------------------------------------------------


async def test_migration_down_and_up(pg: PgDatabase) -> None:
    _alembic(pg, "downgrade", "8_1_keys")
    tables = await as_migrator(
        pg.migrator_url,
        "SELECT count(*) FROM pg_tables WHERE tablename IN ('spaces', 'space_members')",
    )
    assert tables == [(0,)]
    _alembic(pg, "upgrade", "head")
    functions = await as_migrator(
        pg.migrator_url,
        "SELECT proname, prosecdef FROM pg_proc "
        "WHERE proname IN ('app_user_id', 'app_space_ids') ORDER BY proname",
    )
    assert functions == [("app_space_ids", True), ("app_user_id", False)]


async def test_one_private_space_per_owner(pg: PgDatabase) -> None:
    user = await make_user("solo")
    with pytest.raises(IntegrityError):
        async with transaction() as tx:
            await tx.execute(
                text("INSERT INTO spaces (kind, owner_user_id) VALUES ('private', :o)"),
                {"o": user.id},
            )
    # Workspaces are not limited.
    await make_workspace("Falcon", user)
    await make_workspace("Kestrel", user)


async def test_private_spaces_have_no_code_name(pg: PgDatabase) -> None:
    user = await make_user("named", private=False)
    with pytest.raises(IntegrityError):
        async with transaction() as tx:
            await tx.execute(
                text(
                    "INSERT INTO spaces (kind, owner_user_id, code_name_enc) "
                    "VALUES ('private', :o, '\\x00')"
                ),
                {"o": user.id},
            )


async def test_key_tables_reject_an_unknown_space(pg: PgDatabase) -> None:
    with pytest.raises(IntegrityError):
        async with transaction() as tx:
            await create_space_key(tx, uuid.uuid4())
    with pytest.raises(IntegrityError):
        async with transaction() as tx:
            await tx.execute(
                text(
                    "INSERT INTO object_keys (object_type, object_id, space_id, wrapped_key) "
                    "VALUES ('document', :o, :s, :w)"
                ),
                {"o": uuid.uuid4(), "s": uuid.uuid4(), "w": b"\x00" * 61},
            )


async def test_deleting_a_user_with_a_private_space_is_restricted(pg: PgDatabase) -> None:
    owner = await make_user("owner1")
    member = await make_user("member1")
    workspace = await make_workspace("Heron", owner, member)
    # A user who owns a space can't be deleted until the space is (RESTRICT)...
    with pytest.raises(IntegrityError):
        await as_migrator(pg.migrator_url, "DELETE FROM users WHERE id = :id", {"id": owner.id})
    # ...while a member's rows go with the user (CASCADE), once their own space is gone.
    await as_migrator(
        pg.migrator_url,
        "DELETE FROM space_keys WHERE space_id IN (SELECT id FROM spaces WHERE owner_user_id = :u)",
        {"u": member.id},
    )
    await as_migrator(
        pg.migrator_url,
        "DELETE FROM object_keys WHERE space_id IN "
        "(SELECT id FROM spaces WHERE owner_user_id = :u)",
        {"u": member.id},
    )
    await as_migrator(
        pg.migrator_url, "DELETE FROM spaces WHERE owner_user_id = :u", {"u": member.id}
    )
    await as_migrator(pg.migrator_url, "DELETE FROM users WHERE id = :id", {"id": member.id})
    rows = await as_user(
        owner.id, "SELECT user_id FROM space_members WHERE space_id = :s", s=workspace
    )
    assert rows == [(owner.id,)]


# --- AC #2: spaces and space_members --------------------------------------------------------


async def test_no_context_sees_nothing(pg: PgDatabase) -> None:
    owner = await make_user("ctxless")
    await make_workspace("Osprey", owner)
    for table in ("spaces", "space_members"):
        assert await as_user(None, f"SELECT * FROM {table}") == []  # noqa: S608
    assert await as_user("", "SELECT * FROM spaces") == []


async def test_malformed_user_id_sees_nothing_without_error(pg: PgDatabase) -> None:
    owner = await make_user("malformed")
    await make_workspace("Plover", owner)
    for value in ("not-a-uuid", "'; DROP TABLE spaces; --", "00000000-0000-0000-0000", " "):
        assert await as_user(value, "SELECT * FROM spaces") == []
        assert await as_user(value, "SELECT app_user_id()") == [(None,)]


async def test_members_see_only_their_spaces(pg: PgDatabase) -> None:
    a = await make_user("alpha")
    b = await make_user("bravo")
    c = await make_user("charlie")
    shared = await make_workspace("Falcon", a, b)
    only_c = await make_workspace("Kestrel", c)

    def ids(rows: list[tuple[uuid.UUID, ...]]) -> set[uuid.UUID]:
        return {r[0] for r in rows}

    a_spaces = ids(await as_user(a.id, "SELECT id FROM spaces"))
    b_spaces = ids(await as_user(b.id, "SELECT id FROM spaces"))
    c_spaces = ids(await as_user(c.id, "SELECT id FROM spaces"))
    assert shared in a_spaces & b_spaces
    assert only_c not in a_spaces | b_spaces
    assert c_spaces.isdisjoint(a_spaces | b_spaces)
    assert len(a_spaces) == 2  # private + Falcon
    # Members of a shared workspace see each other's membership rows, nothing else.
    members = await as_user(a.id, "SELECT space_id, user_id FROM space_members")
    assert {(shared, a.id), (shared, b.id)} <= set(members)
    assert all(space in a_spaces for space, _ in members)


async def test_non_members_cant_update_or_delete(pg: PgDatabase) -> None:
    a = await make_user("upd_a")
    b = await make_user("upd_b")
    workspace = await make_workspace("Egret", a)
    async with transaction(context={"app.user_id": str(b.id)}) as tx:
        result = await tx.execute(
            text("UPDATE spaces SET retention_days = 7 WHERE id = :id"), {"id": workspace}
        )
        assert getattr(result, "rowcount", None) == 0
        result = await tx.execute(
            text("DELETE FROM space_members WHERE space_id = :id"), {"id": workspace}
        )
        assert getattr(result, "rowcount", None) == 0


async def test_closed_spaces_vanish(pg: PgDatabase) -> None:
    a = await make_user("closer")
    b = await make_user("closed_member")
    workspace = await make_workspace("Swift", a, b)
    await close_space(a, workspace)
    for user in (a, b):
        visible = {r[0] for r in await as_user(user.id, "SELECT id FROM spaces")}
        assert workspace not in visible
        assert (
            await as_user(user.id, "SELECT 1 FROM space_members WHERE space_id = :s", s=workspace)
            == []
        )
    # The owner (app_migrator) still sees it: spaces doesn't FORCE RLS (see app.spaces.rls).
    assert await as_migrator(
        pg.migrator_url, "SELECT count(*) FROM spaces WHERE id = :id", {"id": workspace}
    ) == [(1,)]


# --- AC #2: a content table created with the helper ---------------------------------------


@pytest.fixture
async def probe(pg: PgDatabase) -> AsyncIterator[str]:
    statements = [
        "CREATE TABLE rls_probe (id uuid PRIMARY KEY, space_id uuid NOT NULL, "
        "owner_user_id uuid, body text)",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON rls_probe TO app_rw",
        *space_rls_statements("rls_probe"),
        "CREATE TABLE rls_probe_owned (id uuid PRIMARY KEY, space_id uuid NOT NULL, "
        "owner_user_id uuid NOT NULL, body text)",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON rls_probe_owned TO app_rw",
        *space_rls_statements("rls_probe_owned", owner_column="owner_user_id"),
    ]
    for statement in statements:
        await as_migrator(pg.migrator_url, statement)
    yield "rls_probe"
    for table in ("rls_probe", "rls_probe_owned"):
        for statement in disable_space_rls_statements(table):
            await as_migrator(pg.migrator_url, statement)
        await as_migrator(pg.migrator_url, f"DROP TABLE {table}")


async def _insert(user_id: uuid.UUID, table: str, space_id: uuid.UUID, body: str) -> None:
    async with transaction(context={"app.user_id": str(user_id)}) as tx:
        await tx.execute(
            text(
                f"INSERT INTO {table} (id, space_id, owner_user_id, body) "  # noqa: S608
                "VALUES (:id, :s, :u, :b)"
            ),
            {"id": uuid.uuid4(), "s": space_id, "u": user_id, "b": body},
        )


async def test_helper_policy_isolates_spaces(pg: PgDatabase, probe: str) -> None:
    a = await make_user("probe_a")
    b = await make_user("probe_b")
    shared = await make_workspace("Tern", a, b)
    a_only = await make_workspace("Gull", a)
    await _insert(a.id, probe, shared, "shared row")
    await _insert(a.id, probe, a_only, "a's row")

    assert {r[0] for r in await as_user(a.id, f"SELECT body FROM {probe}")} == {  # noqa: S608
        "shared row",
        "a's row",
    }
    assert [r[0] for r in await as_user(b.id, f"SELECT body FROM {probe}")] == ["shared row"]  # noqa: S608
    assert await as_user(None, f"SELECT * FROM {probe}") == []  # noqa: S608
    # FORCE: the table owner sees nothing without context either.
    assert await as_migrator(pg.migrator_url, f"SELECT count(*) FROM {probe}") == [(0,)]  # noqa: S608

    # WITH CHECK: b can't write into a space b isn't in.
    with pytest.raises(DBAPIError, match="row-level security"):
        await _insert(b.id, probe, a_only, "smuggled")


async def test_helper_owner_column_is_owner_only_inside_a_workspace(
    pg: PgDatabase, probe: str
) -> None:
    a = await make_user("own_a")
    b = await make_user("own_b")
    shared = await make_workspace("Crane", a, b)
    await _insert(a.id, "rls_probe_owned", shared, "a's conversation")
    await _insert(b.id, "rls_probe_owned", shared, "b's conversation")
    assert [r[0] for r in await as_user(a.id, "SELECT body FROM rls_probe_owned")] == [
        "a's conversation"
    ]
    assert [r[0] for r in await as_user(b.id, "SELECT body FROM rls_probe_owned")] == [
        "b's conversation"
    ]


async def test_helper_content_vanishes_with_a_closed_space(pg: PgDatabase, probe: str) -> None:
    a = await make_user("vanish")
    workspace = await make_workspace("Rail", a)
    await _insert(a.id, probe, workspace, "row")
    await close_space(a, workspace)
    assert await as_user(a.id, f"SELECT * FROM {probe}") == []  # noqa: S608


def test_helper_rejects_unsafe_identifiers() -> None:
    for bad in ("docs; DROP TABLE x", "Docs", "", "a" * 64, "docs-1"):
        with pytest.raises(ValueError, match="identifier"):
            space_rls_statements(bad)
    with pytest.raises(ValueError, match="identifier"):
        space_rls_statements("docs", owner_column="owner; --")


def test_helper_statements() -> None:
    assert space_rls_statements("documents") == [
        "ALTER TABLE documents ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE documents FORCE ROW LEVEL SECURITY",
        "CREATE POLICY documents_space_isolation ON documents USING "
        "(space_id = ANY ((SELECT app_space_ids())::uuid[])) "
        "WITH CHECK (space_id = ANY ((SELECT app_space_ids())::uuid[]))",
    ]
    owned = space_rls_statements("conversations", owner_column="owner_user_id")[2]
    assert "AND owner_user_id = (SELECT app_user_id())" in owned
