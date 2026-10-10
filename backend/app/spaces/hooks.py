"""Core-event subscriptions of the spaces module.

``auth.user_created`` (emitted by user creation, Story 2.3, with ``session=`` the
creating transaction) creates the user's private space in that same transaction, so a
rollback leaves neither behind.
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.spaces.service import create_private_space

USER_CREATED = "auth.user_created"


async def on_user_created(*, session: AsyncSession, user_id: uuid.UUID, **_: Any) -> None:
    await create_private_space(session, user_id)


unsubscribe_user_created = events.subscribe(USER_CREATED, on_user_created)
