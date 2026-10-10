"""User administration (FR-001, FR-003, FR-005): create, list, update, links, revocations.

Every function takes the acting admin's ID and records an ``admin.*`` audit event in
the same transaction as the change. Nothing here reads or returns content: users are
metadata, and a user's spaces are reported as counts only.

Guards (``409``): an admin can't demote or deactivate themselves, and the last admin
can't be demoted or deactivated. Changes that could remove an admin first lock every
remaining admin row (in ID order), so two admins demoting each other at once are
serialised and one of them gets ``last_admin``.

Errors are :class:`~app.core.errors.ProblemException`, which the routes pass through and
the bootstrap command prints.
"""

import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import app.spaces  # noqa: F401 (subscribes the private-space hook to auth.user_created)
from app.admin.audit_events import LinkIssued, SessionsRevoked, UserCreated, UserUpdated
from app.admin.settings import get_admin_settings
from app.audit import writer
from app.auth import links, repository
from app.auth.models import Role, TokenPurpose, User, UserStatus
from app.auth.passwords import hash_password
from app.auth.service import USER_TARGET, correlation_id, normalise_username
from app.auth.tokens import generate_token
from app.core import events
from app.core.db import transaction
from app.core.errors import ProblemException
from app.core.timeutil import utcnow
from app.spaces.models import Space, SpaceKind, SpaceMember, SpaceRole

USER_CREATED_EVENT = "auth.user_created"
SET_PASSWORD_PATH = "/set-password"  # noqa: S105 (a URL path)
# Admins who can still act: an invited admin hasn't signed in yet, a deactivated one never will.
_EFFECTIVE_ADMIN_STATUSES = (UserStatus.ACTIVE, UserStatus.LOCKED)


@dataclass(frozen=True, slots=True)
class IssuedLink:
    url: str  # https://<site>/set-password#t=<token>: shown once, never stored or logged
    purpose: TokenPurpose
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SpaceCounts:
    """A user's open spaces, as counts (FR-003: the admin is prompted about them)."""

    private_spaces: int
    owned_workspaces: int
    sole_owner_workspaces: int  # workspaces that would be left without an owner
    member_workspaces: int


@dataclass(frozen=True, slots=True)
class UserChange:
    user: User
    spaces: SpaceCounts


def link_url(token: str) -> str:
    return f"{get_admin_settings().site_url.rstrip('/')}{SET_PASSWORD_PATH}#t={token}"


def _not_found() -> ProblemException:
    return ProblemException(HTTPStatus.NOT_FOUND, "user_not_found", "User not found")


# --- Create -------------------------------------------------------------------------------


async def create_user(
    actor_id: uuid.UUID | None,
    *,
    username: str,
    email: str,
    role: Role,
    now: datetime | None = None,
) -> tuple[User, IssuedLink]:
    """Create an invited user, their private space (via ``auth.user_created``) and an
    invite link, all in one transaction. ``409 user_exists`` names the clashing field."""
    async with transaction() as tx:
        return await create_user_in(
            tx, actor_id, username=username, email=email, role=role, now=now
        )


async def create_user_in(
    tx: AsyncSession,
    actor_id: uuid.UUID | None,
    *,
    username: str,
    email: str,
    role: Role,
    bootstrap: bool = False,
    now: datetime | None = None,
) -> tuple[User, IssuedLink]:
    now = now or utcnow()
    try:
        user = await repository.create_user(
            tx,
            username=normalise_username(username),
            email=unicodedata.normalize("NFKC", email).strip(),
            role=role,
        )
    except repository.DuplicateUserError as exc:
        raise ProblemException(
            HTTPStatus.CONFLICT, "user_exists", "User already exists", field=exc.field
        ) from None
    await events.emit(USER_CREATED_EVENT, session=tx, user_id=user.id)
    await writer.record(
        UserCreated(role=role, bootstrap=bootstrap),
        actor_user_id=actor_id,
        correlation_id=correlation_id(),
        target_type=USER_TARGET,
        target_id=user.id,
        session=tx,
    )
    link = await _issue(tx, actor_id, user, now)
    return user, link


async def _issue(
    tx: AsyncSession, actor_id: uuid.UUID | None, user: User, now: datetime
) -> IssuedLink:
    try:
        issued = await links.issue_token(tx, user, created_by=actor_id, now=now)
    except links.LinkNotAllowedError:
        raise ProblemException(
            HTTPStatus.CONFLICT, "user_deactivated", "User is deactivated"
        ) from None
    await writer.record(
        LinkIssued(purpose=issued.purpose, expires_at=issued.expires_at),
        actor_user_id=actor_id,
        correlation_id=correlation_id(),
        target_type=USER_TARGET,
        target_id=user.id,
        session=tx,
    )
    return IssuedLink(link_url(issued.token), issued.purpose, issued.expires_at)


# --- Read ---------------------------------------------------------------------------------


async def list_users() -> Sequence[User]:
    """Every user, oldest first. Users carry no content, so this is safe to return."""
    async with transaction() as tx:
        return (await tx.execute(select(User).order_by(User.created_at, User.id))).scalars().all()


async def space_counts(user_id: uuid.UUID) -> SpaceCounts:
    """``user_id``'s open spaces as counts.

    Memberships are visible only to members (RLS), so this reads under the user's own
    RLS context, like ``spaces.deps.load_space_ids``. Only counts leave this function.
    """
    async with transaction(context={"app.user_id": str(user_id)}) as tx:
        owners = (
            select(func.count())
            .where(SpaceMember.space_id == Space.id, SpaceMember.role == SpaceRole.OWNER)
            .correlate(Space)
            .scalar_subquery()
        )
        rows = (
            await tx.execute(
                select(Space.kind, SpaceMember.role, owners)
                .join(SpaceMember, SpaceMember.space_id == Space.id)
                .where(SpaceMember.user_id == user_id, Space.closed_at.is_(None))
            )
        ).all()
    private = sum(1 for kind, _, _ in rows if kind is SpaceKind.PRIVATE)
    workspaces = [(role, n) for kind, role, n in rows if kind is SpaceKind.WORKSPACE]
    return SpaceCounts(
        private_spaces=private,
        owned_workspaces=sum(1 for role, _ in workspaces if role is SpaceRole.OWNER),
        sole_owner_workspaces=sum(
            1 for role, n in workspaces if role is SpaceRole.OWNER and n == 1
        ),
        member_workspaces=sum(1 for role, _ in workspaces if role is SpaceRole.MEMBER),
    )


# --- Update -------------------------------------------------------------------------------


async def _lock_effective_admins(tx: AsyncSession) -> list[uuid.UUID]:
    return list(
        (
            await tx.execute(
                select(User.id)
                .where(User.role == Role.ADMIN, User.status.in_(_EFFECTIVE_ADMIN_STATUSES))
                .order_by(User.id)
                .with_for_update()
            )
        ).scalars()
    )


async def _lock_user(tx: AsyncSession, user_id: uuid.UUID) -> User:
    user = (
        await tx.execute(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if user is None:
        raise _not_found()
    return user


def _check_guards(
    actor_id: uuid.UUID, target: User, admins: list[uuid.UUID], *, removes_admin: bool
) -> None:
    if not removes_admin:
        return
    if target.id == actor_id:
        raise ProblemException(
            HTTPStatus.CONFLICT, "cannot_demote_self", "You can't remove your own admin access"
        )
    if target.id in admins and len(admins) == 1:
        raise ProblemException(HTTPStatus.CONFLICT, "last_admin", "The last admin can't be removed")


def _status_after(target: User, requested: UserStatus | None) -> UserStatus | None:
    """The new status for a requested ``active``/``deactivated``, or ``None`` if unchanged."""
    if requested is UserStatus.DEACTIVATED:
        return None if target.status is UserStatus.DEACTIVATED else UserStatus.DEACTIVATED
    if requested is UserStatus.ACTIVE and target.status in {
        UserStatus.DEACTIVATED,
        UserStatus.LOCKED,
    }:
        return UserStatus.ACTIVE
    return None


async def update_user(
    actor_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    role: Role | None = None,
    status: UserStatus | None = None,
    unlock: bool = False,
    now: datetime | None = None,
) -> UserChange:
    """Change role and/or status (``active``/``deactivated``) and/or unlock.

    Deactivation revokes every session and expires every link in the same transaction,
    so the user's next request is a 401. Takes effect on the next request either way:
    the session check re-reads the user.
    """
    if status not in {None, UserStatus.ACTIVE, UserStatus.DEACTIVATED}:
        raise ValueError("status must be active or deactivated")
    now = now or utcnow()
    async with transaction() as tx:
        # Admin rows first, then the target: the same lock order in every transaction.
        admins = await _lock_effective_admins(tx)
        target = await _lock_user(tx, user_id)
        new_role = role if role is not None and role is not target.role else None
        new_status = _status_after(target, status)
        removes_admin = target.role is Role.ADMIN and (
            new_role is not None or new_status is UserStatus.DEACTIVATED
        )
        _check_guards(actor_id, target, admins, removes_admin=removes_admin)

        unlocked = unlock and (
            target.failed_login_count > 0
            or target.locked_until is not None
            or target.status is UserStatus.LOCKED
        )
        values: dict[str, object] = {}
        if new_role is not None:
            values["role"] = new_role
        if new_status is not None:
            values["status"] = new_status
        if new_status is UserStatus.DEACTIVATED and target.password_hash is None:
            # Only invited users may lack a password (users table constraint). An invited
            # user being deactivated gets the hash of a random secret nobody ever sees:
            # after re-activation they need a reset link to sign in.
            values["password_hash"] = hash_password(generate_token())
        if unlocked:
            values |= {"failed_login_count": 0, "locked_until": None}
            if target.status is UserStatus.LOCKED and new_status is None:
                values["status"] = new_status = UserStatus.ACTIVE
        revoked = 0
        if values:
            await tx.execute(
                update(User)
                .where(User.id == user_id)
                .values(updated_at=now, **values)
                .execution_options(synchronize_session="fetch")
            )
            if new_status is UserStatus.DEACTIVATED:
                revoked = await repository.revoke_all_sessions(tx, user_id, now=now)
                await repository.invalidate_password_tokens(tx, user_id, now=now)
            await writer.record(
                UserUpdated(
                    new_role=new_role,
                    new_status=new_status,
                    unlocked=unlocked,
                    sessions_revoked=revoked,
                ),
                actor_user_id=actor_id,
                correlation_id=correlation_id(),
                target_type=USER_TARGET,
                target_id=user_id,
                session=tx,
            )
        await tx.refresh(target)
    return UserChange(target, await space_counts(user_id))


# --- Links and sessions ---------------------------------------------------------------------


async def issue_link(
    actor_id: uuid.UUID, user_id: uuid.UUID, *, now: datetime | None = None
) -> IssuedLink:
    """A new link (``invite`` while invited, else ``reset``); earlier links stop working."""
    async with transaction() as tx:
        user = await _lock_user(tx, user_id)
        return await _issue(tx, actor_id, user, now or utcnow())


async def revoke_sessions(
    actor_id: uuid.UUID, user_id: uuid.UUID, *, now: datetime | None = None
) -> int:
    """Revoke every session of ``user_id``; returns how many were active."""
    async with transaction() as tx:
        await _lock_user(tx, user_id)
        count = await repository.revoke_all_sessions(tx, user_id, now=now)
        await writer.record(
            SessionsRevoked(count=count),
            actor_user_id=actor_id,
            correlation_id=correlation_id(),
            target_type=USER_TARGET,
            target_id=user_id,
            session=tx,
        )
    return count
