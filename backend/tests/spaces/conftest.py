"""Fixtures for the spaces tests: users, workspaces, raw SQL as app_rw (RLS applies)
or as app_migrator (the table owner), and the auth fixtures for API tests."""

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth import repository
from app.auth.models import Role, User, UserStatus
from app.auth.passwords import hash_password
from app.core.db import transaction
from app.crypto.fields import encrypt_field
from app.crypto.keys import create_object_key, create_space_key
from app.spaces.models import SpaceRole
from app.spaces.service import CODE_NAME_FIELD, SPACE_OBJECT_TYPE, create_private_space
from tests.audit.conftest import audit_key
from tests.auth.conftest import PASSWORD, auth_env, auth_settings_env
from tests.conftest import PgDatabase
from tests.crypto.conftest import kek_bytes

__all__ = ["audit_key", "auth_env", "auth_settings_env", "kek_bytes"]


@pytest.fixture
async def spaces_db(pg: PgDatabase, kek_bytes: bytes, audit_key: bytes) -> PgDatabase:
    return pg


async def make_user(name: str, *, role: Role = Role.USER, private: bool = True) -> User:
    """An active user (password ``PASSWORD``), with a private space unless told not to."""
    async with transaction() as tx:
        user = await repository.create_user(
            tx,
            username=name,
            email=f"{name}@example.test",
            role=role,
            status=UserStatus.ACTIVE,
            password_hash=hash_password(PASSWORD),
        )
        if private:
            await create_private_space(tx, user.id)
    return user


async def make_workspace(
    code_name: str, owner: User, *members: User, closed: bool = False
) -> uuid.UUID:
    """A workspace with an encrypted code name, owned by ``owner``."""
    space_id = uuid.uuid4()
    async with transaction(context={"app.user_id": str(owner.id)}) as tx:
        await tx.execute(
            text("INSERT INTO spaces (id, kind, owner_user_id) VALUES (:id, 'workspace', :o)"),
            {"id": space_id, "o": owner.id},
        )
        rows = [(owner.id, SpaceRole.OWNER), *((m.id, SpaceRole.MEMBER) for m in members)]
        for user_id, role in rows:
            await tx.execute(
                text(
                    "INSERT INTO space_members (space_id, user_id, role, added_by) "
                    "VALUES (:s, :u, :r, :by)"
                ),
                {"s": space_id, "u": user_id, "r": role.value, "by": owner.id},
            )
        await create_space_key(tx, space_id)
        ref = await create_object_key(tx, space_id, SPACE_OBJECT_TYPE, space_id)
        encrypted = await encrypt_field(ref, CODE_NAME_FIELD, code_name, executor=tx)
        await tx.execute(
            text("UPDATE spaces SET code_name_enc = :c, closed_at = :closed WHERE id = :id"),
            {"c": encrypted, "id": space_id, "closed": None},
        )
    if closed:
        await close_space(owner, space_id)
    return space_id


async def close_space(owner: User, space_id: uuid.UUID) -> None:
    """Close a workspace as its owner, through app_rw (the update policy must allow it)."""
    async with transaction(context={"app.user_id": str(owner.id)}) as tx:
        result = await tx.execute(
            text("UPDATE spaces SET closed_at = now() WHERE id = :id"), {"id": space_id}
        )
        assert getattr(result, "rowcount", 0) == 1


async def as_migrator(url: str, sql: str, params: dict[str, Any] | None = None) -> Any:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), params or {})
            return result.all() if result.returns_rows else result.rowcount
    finally:
        await engine.dispose()


async def as_user(user_id: uuid.UUID | str | None, sql: str, **params: Any) -> list[Any]:
    """Rows of ``sql`` run as app_rw with ``app.user_id`` set (or no context at all)."""
    context = None if user_id is None else {"app.user_id": str(user_id)}
    async with transaction(context=context) as tx:
        return list((await tx.execute(text(sql), params)).all())
