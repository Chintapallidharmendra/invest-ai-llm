"""``/admin/users``: create, list, update, set-password links and session revocations
(FR-001, FR-003, FR-005). Admins only; a non-admin gets 403 and an audit row.

Responses that carry a set-password link are ``Cache-Control: no-store``: the link is
shown once and must not linger in any cache.
"""

import uuid
from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, Depends, Response

from app.admin import service_users
from app.admin.schemas import (
    AdminUser,
    AdminUserList,
    CreatedUser,
    CreateUserRequest,
    SessionRevocation,
    SetPasswordLink,
    SpaceCounts,
    UpdatedUser,
    UpdateUserRequest,
)
from app.auth.deps import CurrentUser, require_role
from app.auth.models import Role, User, UserStatus
from app.core.errors import Problem
from app.core.timeutil import utcnow

router = APIRouter(prefix="/admin/users", tags=["admin"])

_admin = require_role(Role.ADMIN)
_ERRORS: dict[int | str, dict[str, Any]] = {
    HTTPStatus.UNAUTHORIZED: {"model": Problem},
    HTTPStatus.FORBIDDEN: {"model": Problem},
}
_NOT_FOUND: dict[int | str, dict[str, Any]] = {HTTPStatus.NOT_FOUND: {"model": Problem}}
_CONFLICT: dict[int | str, dict[str, Any]] = {HTTPStatus.CONFLICT: {"model": Problem}}
_NO_STORE = "no-store"


def _user(user: User) -> AdminUser:
    now = utcnow()
    return AdminUser(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        status=user.status,
        locked=user.status is UserStatus.LOCKED
        or (user.locked_until is not None and user.locked_until > now),
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


def _link(link: service_users.IssuedLink) -> SetPasswordLink:
    return SetPasswordLink(url=link.url, purpose=link.purpose, expires_at=link.expires_at)


@router.post(
    "",
    status_code=HTTPStatus.CREATED,
    responses={**_ERRORS, **_CONFLICT},
)
async def create_user(
    body: CreateUserRequest,
    response: Response,
    admin: CurrentUser = Depends(_admin),  # noqa: B008
) -> CreatedUser:
    user, link = await service_users.create_user(
        admin.id, username=body.username, email=body.email, role=body.role
    )
    response.headers["Cache-Control"] = _NO_STORE
    return CreatedUser(user=_user(user), link=_link(link))


@router.get("", responses=_ERRORS)
async def list_users(admin: CurrentUser = Depends(_admin)) -> AdminUserList:  # noqa: B008
    return AdminUserList(items=[_user(u) for u in await service_users.list_users()])


@router.patch("/{user_id}", responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT})
async def update_user(
    user_id: uuid.UUID,
    body: UpdateUserRequest,
    admin: CurrentUser = Depends(_admin),  # noqa: B008
) -> UpdatedUser:
    change = await service_users.update_user(
        admin.id, user_id, role=body.role, status=body.status, unlock=body.unlock
    )
    counts = change.spaces
    return UpdatedUser(
        user=_user(change.user),
        spaces=SpaceCounts(
            private_spaces=counts.private_spaces,
            owned_workspaces=counts.owned_workspaces,
            sole_owner_workspaces=counts.sole_owner_workspaces,
            member_workspaces=counts.member_workspaces,
        ),
    )


@router.post(
    "/{user_id}/set-password-links",
    status_code=HTTPStatus.CREATED,
    responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT},
)
async def issue_link(
    user_id: uuid.UUID,
    response: Response,
    admin: CurrentUser = Depends(_admin),  # noqa: B008
) -> SetPasswordLink:
    link = await service_users.issue_link(admin.id, user_id)
    response.headers["Cache-Control"] = _NO_STORE
    return _link(link)


@router.post("/{user_id}/session-revocations", responses={**_ERRORS, **_NOT_FOUND})
async def revoke_sessions(
    user_id: uuid.UUID,
    admin: CurrentUser = Depends(_admin),  # noqa: B008
) -> SessionRevocation:
    return SessionRevocation(revoked=await service_users.revoke_sessions(admin.id, user_id))
