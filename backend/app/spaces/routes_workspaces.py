"""Deal workspace endpoints (FR-040): ``POST /spaces``, ``PATCH``/``DELETE /spaces/{id}``,
``GET``/``POST /spaces/{id}/members``, ``DELETE /spaces/{id}/members/{user_id}``.

Mounted by ``spaces/routes.py``. A space the caller isn't a member of is a 404, whatever
it is; owner-only actions by a plain member are a 403.
"""

import uuid
from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, Depends, Response

from app.auth.deps import CurrentUser, require_role
from app.auth.models import Role
from app.core.errors import Problem, ProblemException
from app.spaces import service_workspaces as workspaces
from app.spaces.access import AccessContext
from app.spaces.deps import access_context, build_access_context
from app.spaces.models import SpaceKind, SpaceRole
from app.spaces.schemas import SpaceOut
from app.spaces.schemas_workspaces import (
    AddMemberRequest,
    CloseRequest,
    CodeNameRequest,
    MemberList,
    MemberOut,
)

router = APIRouter(prefix="/spaces", tags=["spaces"])

_STATUS: dict[type[workspaces.WorkspaceError], tuple[HTTPStatus, str]] = {
    workspaces.SpaceNotFoundError: (HTTPStatus.NOT_FOUND, "Space not found"),
    workspaces.UserNotFoundError: (HTTPStatus.NOT_FOUND, "User not found"),
    workspaces.NotOwnerError: (HTTPStatus.FORBIDDEN, "Only owners can do this"),
    workspaces.LastOwnerError: (HTTPStatus.CONFLICT, "A workspace needs at least one owner"),
    workspaces.PrivateSpaceError: (HTTPStatus.CONFLICT, "Not possible for a private space"),
    workspaces.InvalidCodeNameError: (
        HTTPStatus.UNPROCESSABLE_CONTENT,
        "Code name must be 1-80 characters",
    ),
}
_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": Problem}
    for status in {HTTPStatus.UNAUTHORIZED, *(s for s, _ in _STATUS.values())}
}


def _problem(exc: workspaces.WorkspaceError) -> ProblemException:
    status, title = _STATUS[type(exc)]
    return ProblemException(status, exc.code, title)


def _member(member: workspaces.Member) -> MemberOut:
    return MemberOut(
        user_id=member.user_id, username=member.username, role=member.role, added_at=member.added_at
    )


async def _user_context(user: CurrentUser = Depends(require_role(Role.USER))) -> AccessContext:  # noqa: B008
    return await build_access_context(user)


@router.post("", status_code=HTTPStatus.CREATED, responses=_RESPONSES)
async def create_workspace(
    body: CodeNameRequest,
    ctx: AccessContext = Depends(_user_context),  # noqa: B008
) -> SpaceOut:
    """Only role ``user`` may create (and so belong to) workspaces."""
    try:
        space_id = await workspaces.create_workspace(ctx, body.code_name)
    except workspaces.WorkspaceError as exc:
        raise _problem(exc) from None
    return SpaceOut(
        id=space_id,
        kind=SpaceKind.WORKSPACE,
        my_role=SpaceRole.OWNER,
        code_name=workspaces.clean_code_name(body.code_name),
    )


@router.patch("/{space_id}", responses=_RESPONSES)
async def rename_workspace(
    space_id: uuid.UUID,
    body: CodeNameRequest,
    ctx: AccessContext = Depends(access_context),  # noqa: B008
) -> SpaceOut:
    try:
        await workspaces.rename_workspace(ctx, space_id, body.code_name)
    except workspaces.WorkspaceError as exc:
        raise _problem(exc) from None
    return SpaceOut(
        id=space_id,
        kind=SpaceKind.WORKSPACE,
        my_role=SpaceRole.OWNER,
        code_name=workspaces.clean_code_name(body.code_name),
    )


@router.delete("/{space_id}", status_code=HTTPStatus.NO_CONTENT, responses=_RESPONSES)
async def close_workspace(
    space_id: uuid.UUID,
    body: CloseRequest,
    ctx: AccessContext = Depends(access_context),  # noqa: B008
) -> Response:
    try:
        await workspaces.close_workspace(ctx, space_id)
    except workspaces.WorkspaceError as exc:
        raise _problem(exc) from None
    return Response(status_code=HTTPStatus.NO_CONTENT)


@router.get("/{space_id}/members", responses=_RESPONSES)
async def list_members(
    space_id: uuid.UUID,
    ctx: AccessContext = Depends(access_context),  # noqa: B008
) -> MemberList:
    try:
        members = await workspaces.list_members(ctx, space_id)
    except workspaces.WorkspaceError as exc:
        raise _problem(exc) from None
    return MemberList(items=[_member(m) for m in members])


@router.post(
    "/{space_id}/members",
    status_code=HTTPStatus.CREATED,
    responses={**_RESPONSES, HTTPStatus.OK: {"model": MemberOut}},
)
async def add_member(
    space_id: uuid.UUID,
    body: AddMemberRequest,
    response: Response,
    ctx: AccessContext = Depends(access_context),  # noqa: B008
) -> MemberOut:
    """201 for a new member; 200 when an existing member's role was set instead."""
    try:
        change = await workspaces.add_member(ctx, space_id, body.username, body.role)
    except workspaces.WorkspaceError as exc:
        raise _problem(exc) from None
    if not change.created:
        response.status_code = HTTPStatus.OK
    return _member(change.member)


@router.delete(
    "/{space_id}/members/{user_id}", status_code=HTTPStatus.NO_CONTENT, responses=_RESPONSES
)
async def remove_member(
    space_id: uuid.UUID,
    user_id: uuid.UUID,
    ctx: AccessContext = Depends(access_context),  # noqa: B008
) -> Response:
    try:
        await workspaces.remove_member(ctx, space_id, user_id)
    except workspaces.WorkspaceError as exc:
        raise _problem(exc) from None
    return Response(status_code=HTTPStatus.NO_CONTENT)
