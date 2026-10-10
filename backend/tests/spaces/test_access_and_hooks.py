"""AC #3 (AccessContext) and AC #4 (private space on user creation)."""

import dataclasses
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.audit.testing import assert_no_content
from app.auth import repository
from app.auth.models import Role, UserStatus
from app.auth.passwords import hash_password
from app.auth.service import SessionUser
from app.core import events
from app.core.db import transaction
from app.crypto.keys import space_key
from app.crypto.models import SpaceKey
from app.spaces.access import AccessContext
from app.spaces.deps import build_access_context, load_space_ids
from app.spaces.hooks import USER_CREATED
from app.spaces.service import create_private_space
from tests.auth.conftest import audit_rows
from tests.conftest import PgDatabase
from tests.spaces.conftest import as_migrator, close_space, make_user, make_workspace

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("spaces_db")]


# --- AC #3 ------------------------------------------------------------------------------


def test_access_context_is_frozen_and_builds_rls_settings() -> None:
    user_id, grant_id = uuid.uuid4(), uuid.uuid4()
    ctx = AccessContext(user_id, Role.USER, frozenset({grant_id}))
    assert ctx.rls_settings() == {"app.user_id": str(user_id)}
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.user_id = uuid.uuid4()  # type: ignore[misc]
    granted = dataclasses.replace(ctx, grant_id=grant_id)
    assert granted.rls_settings() == {"app.user_id": str(user_id), "app.grant_id": str(grant_id)}
    assert ctx.can_access(grant_id)
    assert not ctx.can_access(user_id)


async def test_rls_settings_are_accepted_by_transaction() -> None:
    user = await make_user("settings")
    ctx = await build_access_context(SessionUser(user.id, user.username, user.role, uuid.uuid4()))
    async with transaction(context={**ctx.rls_settings(), "app.grant_id": str(uuid.uuid4())}) as tx:
        current = (await tx.execute(text("SELECT app_user_id()"))).scalar_one()
    assert current == user.id


async def test_membership_is_reread_every_time() -> None:
    owner = await make_user("lead")
    member = await make_user("analyst")
    workspace = await make_workspace("Falcon", owner, member)
    assert workspace in await load_space_ids(member.id)

    async with transaction(context={"app.user_id": str(owner.id)}) as tx:
        await tx.execute(
            text("DELETE FROM space_members WHERE space_id = :s AND user_id = :u"),
            {"s": workspace, "u": member.id},
        )
    assert workspace not in await load_space_ids(member.id)
    assert workspace in await load_space_ids(owner.id)


async def test_closed_workspaces_are_left_out() -> None:
    owner = await make_user("closes")
    workspace = await make_workspace("Kite", owner)
    await close_space(owner, workspace)
    ids = await load_space_ids(owner.id)
    assert workspace not in ids
    assert len(ids) == 1  # just the private space


# --- AC #4 ------------------------------------------------------------------------------


async def _new_user(tx: object, name: str) -> uuid.UUID:
    user = await repository.create_user(
        tx,  # type: ignore[arg-type]
        username=name,
        email=f"{name}@example.test",
        role=Role.USER,
        status=UserStatus.ACTIVE,
        password_hash=hash_password("not used in this test"),
    )
    return user.id


async def _private_space_of(pg_url: str, user_id: uuid.UUID) -> list[tuple[object, ...]]:
    return await as_migrator(
        pg_url,
        "SELECT s.id, m.role FROM spaces s JOIN space_members m ON m.space_id = s.id "
        "WHERE s.owner_user_id = :u AND s.kind = 'private'",
        {"u": user_id},
    )


async def test_user_created_event_creates_the_private_space(pg: PgDatabase) -> None:
    admin = await make_user("admin1", role=Role.ADMIN)
    # The emitter runs under the admin's context, which can't see the new rows.
    async with transaction(context={"app.user_id": str(admin.id)}) as tx:
        user_id = await _new_user(tx, "newcomer")
        await events.emit(USER_CREATED, session=tx, user_id=user_id)
    [(space_id, role)] = await _private_space_of(pg.migrator_url, user_id)
    assert role == "owner"
    async with transaction() as tx:
        key_rows = (await tx.execute(select(SpaceKey).where(SpaceKey.space_id == space_id))).all()
    assert len(key_rows) == 1
    assert len(await space_key(space_id)) == 32
    assert await load_space_ids(user_id) == frozenset({space_id})

    [event] = [
        e
        for e in await audit_rows("spaces.private_created")
        if e.payload["owner_user_id"] == str(user_id)
    ]
    assert event.payload == {"space_id": str(space_id), "owner_user_id": str(user_id)}
    assert event.target_id == space_id
    assert_no_content(event.payload)


async def test_rollback_leaves_nothing(pg: PgDatabase) -> None:
    class _AbortError(Exception):
        pass

    async def create_then_abort() -> None:
        async with transaction() as tx:
            user_id = await _new_user(tx, "rolled_back")
            await events.emit(USER_CREATED, session=tx, user_id=user_id)
            raise _AbortError

    with pytest.raises(_AbortError):
        await create_then_abort()
    url = pg.migrator_url
    for table in ("users", "spaces", "space_members", "space_keys"):
        assert await as_migrator(url, f"SELECT count(*) FROM {table}") == [(0,)]  # noqa: S608
    assert await audit_rows("spaces.private_created") == []


async def test_creating_twice_is_a_no_op(pg: PgDatabase) -> None:
    async with transaction() as tx:
        user_id = await _new_user(tx, "twice")
        first = await create_private_space(tx, user_id)
        second = await create_private_space(tx, user_id)
        await events.emit(USER_CREATED, session=tx, user_id=user_id)
    assert first is not None
    assert second is None
    rows = await _private_space_of(pg.migrator_url, user_id)
    assert [r[0] for r in rows] == [first]
    assert len(await audit_rows("spaces.private_created")) == 1


async def test_a_failing_hook_fails_the_emitter() -> None:
    # Subscribers fail closed: no private space means no user.
    async def emit_for_a_missing_user() -> None:
        async with transaction() as tx:
            await events.emit(USER_CREATED, session=tx, user_id=uuid.uuid4())

    with pytest.raises(IntegrityError):
        await emit_for_a_missing_user()
