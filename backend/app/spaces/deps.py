"""``access_context``: the FastAPI dependency that builds :class:`AccessContext`.

Memberships are re-read on every request (no cache), so removing a member or closing
a workspace takes effect on the next request.
"""

import uuid

from fastapi import Depends
from sqlalchemy import select

from app.auth.deps import CurrentUser, current_user
from app.core.db import transaction
from app.spaces.access import AccessContext
from app.spaces.models import Space, SpaceMember


async def load_space_ids(user_id: uuid.UUID) -> frozenset[uuid.UUID]:
    """The open spaces ``user_id`` belongs to, read under that user's RLS context."""
    async with transaction(context={"app.user_id": str(user_id)}) as tx:
        rows = await tx.execute(
            select(SpaceMember.space_id)
            .join(Space, Space.id == SpaceMember.space_id)
            .where(SpaceMember.user_id == user_id, Space.closed_at.is_(None))
        )
        return frozenset(rows.scalars())


async def build_access_context(user: CurrentUser) -> AccessContext:
    return AccessContext(user.id, user.role, await load_space_ids(user.id))


async def access_context(user: CurrentUser = Depends(current_user)) -> AccessContext:  # noqa: B008
    return await build_access_context(user)
