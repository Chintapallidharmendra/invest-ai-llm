"""Deal workspaces (FR-040, ADR-023): create, rename, members, close.

Every function takes the caller's :class:`AccessContext` and runs under its RLS context,
so the database enforces the same boundary. A space the caller isn't a member of (or
that is closed) doesn't exist for them: :class:`SpaceNotFoundError`, which routes turn
into a 404. A member who isn't an owner gets :class:`NotOwnerError` (403) for
owner-only actions.

Member changes lock the space row first, so concurrent changes to one workspace run one
after the other and the "last owner" rule can't be raced: of two owners removing each
other at once, one gets :class:`LastOwnerError`.

Code names are encrypted under the object key ``("space", space_id)`` (field
``code_name``, see :func:`app.spaces.service.code_name_ref`). Audit events carry IDs and
enums only, never a code name.
"""

import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer
from app.audit.types import AuditEvent
from app.auth.models import Role, User, UserStatus
from app.auth.service import correlation_id
from app.core.db import transaction
from app.core.ids import new_uuid7
from app.core.timeutil import utcnow
from app.crypto.fields import encrypt_field
from app.crypto.keys import create_object_key, create_space_key
from app.jobs import queue
from app.jobs import registry as job_registry
from app.spaces.access import AccessContext
from app.spaces.audit_events_workspaces import (
    MemberAdded,
    MemberRemoved,
    MemberRoleChanged,
    WorkspaceClosed,
    WorkspaceCreated,
    WorkspaceRenamed,
)
from app.spaces.models import Space, SpaceKind, SpaceMember, SpaceRole
from app.spaces.service import CODE_NAME_FIELD, SPACE_OBJECT_TYPE

CODE_NAME_MAX_LENGTH = 80
SPACE_TARGET = "space"
# Registered by the deletion pipeline (7.4); until then closed_at alone hides everything.
DELETE_SPACE_JOB = "lifecycle_delete_space"


class WorkspaceError(Exception):
    """Base of the expected failures; ``code`` is the problem code."""

    code = "workspace_error"


class SpaceNotFoundError(WorkspaceError):
    code = "space_not_found"


class NotOwnerError(WorkspaceError):
    code = "not_space_owner"


class UserNotFoundError(WorkspaceError):
    code = "user_not_found"


class LastOwnerError(WorkspaceError):
    code = "last_owner"


class PrivateSpaceError(WorkspaceError):
    code = "private_space"


class InvalidCodeNameError(WorkspaceError):
    code = "invalid_code_name"


@dataclass(frozen=True, slots=True)
class Member:
    user_id: uuid.UUID
    username: str
    role: SpaceRole
    added_at: datetime


@dataclass(frozen=True, slots=True)
class MemberChange:
    member: Member
    created: bool  # False: an existing member's role was changed (or left as it was)


def clean_code_name(code_name: str) -> str:
    """Trimmed; 1-80 characters and no control characters, else :class:`InvalidCodeNameError`."""
    name = code_name.strip()
    if not 1 <= len(name) <= CODE_NAME_MAX_LENGTH or any(
        unicodedata.category(c) == "Cc" for c in name
    ):
        raise InvalidCodeNameError
    return name


async def _record(
    tx: AsyncSession, ctx: AccessContext, space_id: uuid.UUID, event: AuditEvent
) -> None:
    await writer.record(
        event,
        actor_user_id=ctx.user_id,
        correlation_id=correlation_id(),
        target_type=SPACE_TARGET,
        target_id=space_id,
        session=tx,
    )


async def _set_code_name(
    tx: AsyncSession, space_id: uuid.UUID, code_name: str, now: datetime
) -> None:
    ref = await create_object_key(tx, space_id, SPACE_OBJECT_TYPE, space_id)
    encrypted = await encrypt_field(ref, CODE_NAME_FIELD, code_name, executor=tx)
    await tx.execute(
        update(Space)
        .where(Space.id == space_id)
        .values(code_name_enc=encrypted, updated_at=now)
        .execution_options(synchronize_session=False)
    )


# --- Create and rename --------------------------------------------------------------------


async def create_workspace(
    ctx: AccessContext, code_name: str, *, now: datetime | None = None
) -> uuid.UUID:
    """A new workspace owned by the caller, with its space key and encrypted code name."""
    name = clean_code_name(code_name)
    now = now or utcnow()
    space_id = new_uuid7()
    async with transaction(context=ctx.rls_settings()) as tx:
        # Order matters: the keys need the space row, and the code name needs the key.
        await tx.execute(
            insert(Space).values(id=space_id, kind=SpaceKind.WORKSPACE, owner_user_id=ctx.user_id)
        )
        await tx.execute(
            insert(SpaceMember).values(
                space_id=space_id, user_id=ctx.user_id, role=SpaceRole.OWNER, added_by=ctx.user_id
            )
        )
        await create_space_key(tx, space_id)
        await _set_code_name(tx, space_id, name, now)
        await _record(tx, ctx, space_id, WorkspaceCreated(space_id=space_id))
    return space_id


async def _lock_workspace(
    tx: AsyncSession, ctx: AccessContext, space_id: uuid.UUID
) -> tuple[Space, SpaceRole]:
    """The open space (row-locked) and the caller's role in it; 404 if not theirs."""
    if not ctx.can_access(space_id):
        raise SpaceNotFoundError
    space = (
        await tx.execute(
            select(Space)
            .where(Space.id == space_id, Space.closed_at.is_(None))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    role = (
        await tx.execute(
            select(SpaceMember.role).where(
                SpaceMember.space_id == space_id, SpaceMember.user_id == ctx.user_id
            )
        )
    ).scalar_one_or_none()
    if space is None or role is None:
        raise SpaceNotFoundError
    return space, role


async def _owned_workspace(tx: AsyncSession, ctx: AccessContext, space_id: uuid.UUID) -> Space:
    space, role = await _lock_workspace(tx, ctx, space_id)
    if space.kind is SpaceKind.PRIVATE:
        raise PrivateSpaceError
    if role is not SpaceRole.OWNER:
        raise NotOwnerError
    return space


async def rename_workspace(
    ctx: AccessContext, space_id: uuid.UUID, code_name: str, *, now: datetime | None = None
) -> None:
    name = clean_code_name(code_name)
    now = now or utcnow()
    async with transaction(context=ctx.rls_settings()) as tx:
        await _owned_workspace(tx, ctx, space_id)
        await _set_code_name(tx, space_id, name, now)
        await _record(tx, ctx, space_id, WorkspaceRenamed(space_id=space_id))


# --- Members ------------------------------------------------------------------------------


def _members_query(space_id: uuid.UUID) -> Select[uuid.UUID, str, SpaceRole, datetime]:
    return (
        select(SpaceMember.user_id, User.username, SpaceMember.role, SpaceMember.added_at)
        .join(User, User.id == SpaceMember.user_id)
        .where(SpaceMember.space_id == space_id)
    )


async def list_members(ctx: AccessContext, space_id: uuid.UUID) -> Sequence[Member]:
    """Owners first, then members, each oldest first. Members of the space only."""
    async with transaction(context=ctx.rls_settings()) as tx:
        await _lock_workspace(tx, ctx, space_id)
        rows = await tx.execute(
            _members_query(space_id).order_by(
                SpaceMember.role, SpaceMember.added_at, SpaceMember.user_id
            )
        )
        return [Member(*row) for row in rows.all()]


async def _owner_count(tx: AsyncSession, space_id: uuid.UUID) -> int:
    return (
        await tx.execute(
            select(func.count()).where(
                SpaceMember.space_id == space_id, SpaceMember.role == SpaceRole.OWNER
            )
        )
    ).scalar_one()


async def add_member(
    ctx: AccessContext,
    space_id: uuid.UUID,
    username: str,
    role: SpaceRole,
    *,
    now: datetime | None = None,
) -> MemberChange:
    """Add an active ``user``-role user by exact username, or change an existing
    member's role. Admins and compliance users can never be members (FR-005)."""
    now = now or utcnow()
    async with transaction(context=ctx.rls_settings()) as tx:
        await _owned_workspace(tx, ctx, space_id)
        user = (
            await tx.execute(
                select(User).where(
                    User.username == username.strip(),
                    User.status == UserStatus.ACTIVE,
                    User.role == Role.USER,
                )
            )
        ).scalar_one_or_none()
        if user is None:
            raise UserNotFoundError
        current = (
            await tx.execute(
                select(SpaceMember.role).where(
                    SpaceMember.space_id == space_id, SpaceMember.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if current is None:
            await tx.execute(
                insert(SpaceMember).values(
                    space_id=space_id, user_id=user.id, role=role, added_by=ctx.user_id
                )
            )
            await _record(
                tx, ctx, space_id, MemberAdded(space_id=space_id, user_id=user.id, role=role)
            )
        elif current is not role:
            if current is SpaceRole.OWNER and await _owner_count(tx, space_id) == 1:
                raise LastOwnerError
            await tx.execute(
                update(SpaceMember)
                .where(SpaceMember.space_id == space_id, SpaceMember.user_id == user.id)
                .values(role=role)
            )
            await _record(
                tx, ctx, space_id, MemberRoleChanged(space_id=space_id, user_id=user.id, role=role)
            )
        row = (
            await tx.execute(_members_query(space_id).where(SpaceMember.user_id == user.id))
        ).one()
        return MemberChange(Member(*row), created=current is None)


async def remove_member(ctx: AccessContext, space_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Owners remove anyone; a member may remove themselves. The last owner stays."""
    async with transaction(context=ctx.rls_settings()) as tx:
        space, my_role = await _lock_workspace(tx, ctx, space_id)
        if space.kind is SpaceKind.PRIVATE:
            raise PrivateSpaceError
        by_self = user_id == ctx.user_id
        if not by_self and my_role is not SpaceRole.OWNER:
            raise NotOwnerError
        role = (
            await tx.execute(
                select(SpaceMember.role).where(
                    SpaceMember.space_id == space_id, SpaceMember.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if role is None:
            raise UserNotFoundError
        if role is SpaceRole.OWNER and await _owner_count(tx, space_id) == 1:
            raise LastOwnerError
        await tx.execute(
            delete(SpaceMember).where(
                SpaceMember.space_id == space_id, SpaceMember.user_id == user_id
            )
        )
        await _record(
            tx, ctx, space_id, MemberRemoved(space_id=space_id, user_id=user_id, by_self=by_self)
        )


# --- Close --------------------------------------------------------------------------------


async def close_workspace(
    ctx: AccessContext, space_id: uuid.UUID, *, now: datetime | None = None
) -> uuid.UUID | None:
    """Close the workspace: it leaves ``app_space_ids()`` at once, so nothing in it is
    visible to anyone. Queues the deletion job when the pipeline (7.4) has registered
    it; returns that job's ID, if any."""
    now = now or utcnow()
    async with transaction(context=ctx.rls_settings()) as tx:
        await _owned_workspace(tx, ctx, space_id)
        await tx.execute(
            update(Space)
            .where(Space.id == space_id)
            .values(closed_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        job_id = None
        if DELETE_SPACE_JOB in job_registry.registered():
            job_id = await queue.enqueue(
                DELETE_SPACE_JOB,
                {"space_id": space_id},
                user_id=ctx.user_id,
                space_id=space_id,
                session=tx,
            )
        await _record(
            tx,
            ctx,
            space_id,
            WorkspaceClosed(space_id=space_id, deletion_queued=job_id is not None),
        )
    return job_id
