"""Conversations API (FR-008, FR-055): list, create, read, rename, delete, and the
selected document set. The SSE ``POST …/messages`` is 3.2.

Only role ``user`` has conversations (admins and compliance have no content access).
Anything not the caller's own, or not in one of their open spaces, is a 404.
"""

import uuid
from http import HTTPStatus
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response

from app.auth.deps import CurrentUser, require_role
from app.auth.models import Role
from app.chat import repository, selected_set
from app.chat.repository import ConversationDetail, ConversationSummary
from app.chat.schemas import (
    ConversationDetailOut,
    ConversationList,
    ConversationOut,
    CreateConversationRequest,
    RenameConversationRequest,
    SelectedDocuments,
    SetDocumentsRequest,
)
from app.core.db import transaction
from app.core.errors import Problem, ProblemException
from app.spaces.access import AccessContext
from app.spaces.deps import build_access_context

router = APIRouter(prefix="/conversations", tags=["conversations"])

_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": Problem}
    for status in (
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_CONTENT,
    )
}


async def _context(user: CurrentUser = Depends(require_role(Role.USER))) -> AccessContext:  # noqa: B008
    return await build_access_context(user)


Context = Annotated[AccessContext, Depends(_context)]


def _not_found() -> ProblemException:
    return ProblemException(
        HTTPStatus.NOT_FOUND, "conversation_not_found", "Conversation not found"
    )


def _summary(c: ConversationSummary) -> ConversationOut:
    return ConversationOut(
        id=c.id,
        space_id=c.space_id,
        title=c.title,
        created_at=c.created_at,
        updated_at=c.updated_at,
        expires_at=c.expires_at,
    )


def _detail(c: ConversationDetail) -> ConversationDetailOut:
    return ConversationDetailOut(**_summary(c).model_dump(), document_ids=list(c.document_ids))


@router.get("", responses=_RESPONSES)
async def list_conversations(
    ctx: Context,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=repository.MAX_PAGE_SIZE)] = repository.DEFAULT_PAGE_SIZE,
) -> ConversationList:
    try:
        async with transaction(context=ctx.rls_settings()) as tx:
            page = await repository.list_conversations(tx, ctx, limit=limit, cursor=cursor)
    except repository.InvalidCursorError:
        raise ProblemException(
            HTTPStatus.UNPROCESSABLE_CONTENT, "invalid_cursor", "Invalid cursor"
        ) from None
    return ConversationList(items=[_summary(c) for c in page.items], next_cursor=page.next_cursor)


@router.post("", status_code=HTTPStatus.CREATED, responses=_RESPONSES)
async def create_conversation(
    body: CreateConversationRequest, ctx: Context
) -> ConversationDetailOut:
    try:
        async with transaction(context=ctx.rls_settings()) as tx:
            created = await repository.create_conversation(tx, ctx, body.space_id)
    except repository.SpaceNotAccessibleError:
        raise ProblemException(HTTPStatus.NOT_FOUND, "space_not_found", "Space not found") from None
    return _detail(created)


@router.get("/{conversation_id}", responses=_RESPONSES)
async def get_conversation(conversation_id: uuid.UUID, ctx: Context) -> ConversationDetailOut:
    try:
        async with transaction(context=ctx.rls_settings()) as tx:
            return _detail(await repository.get_conversation(tx, ctx, conversation_id))
    except repository.ConversationNotFoundError:
        raise _not_found() from None


@router.patch("/{conversation_id}", responses=_RESPONSES)
async def rename_conversation(
    conversation_id: uuid.UUID, body: RenameConversationRequest, ctx: Context
) -> ConversationDetailOut:
    try:
        title = repository.clean_title(body.title)
    except ValueError:
        raise ProblemException(
            HTTPStatus.UNPROCESSABLE_CONTENT, "invalid_title", "Title must be 1-200 characters"
        ) from None
    try:
        async with transaction(context=ctx.rls_settings()) as tx:
            await repository.rename_conversation(tx, ctx, conversation_id, title)
            return _detail(await repository.get_conversation(tx, ctx, conversation_id))
    except repository.ConversationNotFoundError:
        raise _not_found() from None


@router.delete("/{conversation_id}", status_code=HTTPStatus.NO_CONTENT, responses=_RESPONSES)
async def delete_conversation(conversation_id: uuid.UUID, ctx: Context) -> Response:
    """Permanent: the conversation's key is shredded and its rows deleted."""
    try:
        async with transaction(context=ctx.rls_settings()) as tx:
            await repository.delete_conversation(tx, ctx, conversation_id)
    except repository.ConversationNotFoundError:
        raise _not_found() from None
    return Response(status_code=HTTPStatus.NO_CONTENT)


@router.put("/{conversation_id}/documents", responses=_RESPONSES)
async def set_documents(
    conversation_id: uuid.UUID, body: SetDocumentsRequest, ctx: Context
) -> SelectedDocuments:
    """Replace the selected set. Every document must be in the conversation's space and
    ready; until document ingestion (9.1) exists, only an empty set is accepted."""
    try:
        async with transaction(context=ctx.rls_settings()) as tx:
            ids = await repository.set_documents(tx, ctx, conversation_id, body.document_ids)
    except repository.ConversationNotFoundError:
        raise _not_found() from None
    except selected_set.DocumentNotFoundError:
        raise ProblemException(
            HTTPStatus.UNPROCESSABLE_CONTENT, "document_not_found", "A document can't be used"
        ) from None
    return SelectedDocuments(document_ids=list(ids))
