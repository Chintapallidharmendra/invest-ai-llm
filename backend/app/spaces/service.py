"""Space creation and listing (ADR-023)."""

import uuid
from dataclasses import dataclass

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer
from app.core.db import transaction
from app.core.ids import new_uuid7
from app.core.obs import current_correlation_id
from app.crypto.fields import decrypt_text
from app.crypto.keys import KeyRef, create_space_key
from app.spaces.access import AccessContext
from app.spaces.audit_events import PrivateSpaceCreated
from app.spaces.models import Space, SpaceKind, SpaceMember, SpaceRole

# A workspace's code name is encrypted under the object key ("space", space_id).
SPACE_OBJECT_TYPE = "space"
CODE_NAME_FIELD = "code_name"
PRIVATE_OWNER_INDEX = "uq_spaces_private_owner"


def _constraint_name(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "__cause__", None) or exc.orig
    name = getattr(diag, "constraint_name", None)
    return name if isinstance(name, str) else None


def code_name_ref(space_id: uuid.UUID) -> KeyRef:
    return KeyRef(space_id, SPACE_OBJECT_TYPE, space_id)


async def create_private_space(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    """Create ``user_id``'s private space in the caller's transaction.

    Creates the space, the owner's membership and the space key, and records
    ``spaces.private_created``. Idempotent: returns the new space's ID, or ``None`` if
    the user already had one (nothing is written then).

    Works under any RLS context (e.g. an admin creating the user): it only inserts,
    and never reads the new rows back.
    """
    space_id = new_uuid7()
    try:
        # A savepoint keeps the caller's transaction usable after a duplicate. Not
        # ON CONFLICT: under RLS that also applies the SELECT policy to the new row,
        # which the creating context (e.g. an admin) can't see.
        async with session.begin_nested():
            await session.execute(
                insert(Space).values(id=space_id, kind=SpaceKind.PRIVATE, owner_user_id=user_id)
            )
    except IntegrityError as exc:
        if _constraint_name(exc) != PRIVATE_OWNER_INDEX:
            raise
        return None
    await session.execute(
        insert(SpaceMember).values(
            space_id=space_id, user_id=user_id, role=SpaceRole.OWNER, added_by=None
        )
    )
    await create_space_key(session, space_id)
    correlation = current_correlation_id()
    await writer.record(
        PrivateSpaceCreated(space_id=space_id, owner_user_id=user_id),
        actor_user_id=None,
        correlation_id=correlation or new_uuid7(),
        target_type="space",
        target_id=space_id,
        session=session,
    )
    return space_id


@dataclass(frozen=True, slots=True)
class SpaceSummary:
    id: uuid.UUID
    kind: SpaceKind
    my_role: SpaceRole
    code_name: str | None


async def list_spaces(ctx: AccessContext) -> list[SpaceSummary]:
    """The caller's private space first, then their open workspaces (oldest first)."""
    async with transaction(context=ctx.rls_settings()) as tx:
        rows = (
            await tx.execute(
                select(Space.id, Space.kind, SpaceMember.role, Space.code_name_enc)
                .join(SpaceMember, SpaceMember.space_id == Space.id)
                .where(
                    SpaceMember.user_id == ctx.user_id,
                    Space.closed_at.is_(None),
                    Space.id.in_(ctx.space_ids),
                )
                .order_by(Space.kind, Space.created_at, Space.id)
            )
        ).all()
        spaces = []
        for space_id, kind, role, code_name_enc in rows:
            code_name = None
            if kind is SpaceKind.WORKSPACE and code_name_enc is not None:
                code_name = await decrypt_text(
                    code_name_ref(space_id), CODE_NAME_FIELD, code_name_enc, executor=tx
                )
            spaces.append(SpaceSummary(space_id, kind, role, code_name))
    return spaces
