"""The conversation's selected set: which documents it may draw on (FR-055).

Documents arrive with 9.1, which registers a :class:`DocumentResolver`. Until then no
document can be checked, so only an **empty** set is accepted::

    from app.chat import selected_set

    selected_set.register_resolver(MyResolver())

A resolver answers, under the caller's RLS context (``session``), which of the given
document IDs are *not* usable: not in the conversation's space, not visible to the
caller, or not ``ready``. Every ID it returns makes the whole update fail.
"""

import uuid
from collections.abc import Collection
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.spaces.access import AccessContext


class DocumentResolver(Protocol):
    async def unusable(
        self,
        session: AsyncSession,
        ctx: AccessContext,
        space_id: uuid.UUID,
        document_ids: Collection[uuid.UUID],
    ) -> set[uuid.UUID]: ...


class DocumentNotFoundError(Exception):
    """Some selected document can't be used (or none can be checked yet)."""


_resolver: DocumentResolver | None = None


def register_resolver(resolver: DocumentResolver) -> None:
    global _resolver  # noqa: PLW0603 (one process-wide registration, made at import)
    _resolver = resolver


def unregister_resolver() -> None:
    global _resolver  # noqa: PLW0603
    _resolver = None


def current_resolver() -> DocumentResolver | None:
    return _resolver


async def check(
    session: AsyncSession,
    ctx: AccessContext,
    space_id: uuid.UUID,
    document_ids: Collection[uuid.UUID],
) -> None:
    """Raise :class:`DocumentNotFoundError` unless every document is usable."""
    if not document_ids:
        return
    if _resolver is None:
        raise DocumentNotFoundError
    if await _resolver.unusable(session, ctx, space_id, document_ids):
        raise DocumentNotFoundError
