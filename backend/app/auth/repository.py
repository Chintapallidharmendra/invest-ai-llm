"""Typed persistence for users, sessions and set-password tokens (ADR-006).

Persistence only: no policy decisions (lockout thresholds, idle timeouts, who may do
what) live here. Every function takes the caller's ``AsyncSession``, so one service call
can combine several steps in one ``transaction()``::

    async with transaction() as tx:
        user = await repository.get_user_by_username(tx, username)
        ...
        await repository.record_successful_login(tx, user.id, now=now)
        await repository.create_session(tx, user_id=user.id, token_hash=..., expires_at=...)

Tokens are passed in already hashed (``app.auth.tokens.hash_token``); raw tokens never
reach this module. Times default to ``utcnow()``; pass ``now`` to control them.
"""

import uuid
from datetime import datetime
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Any, Final

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import (
    PasswordToken,
    Role,
    TokenPurpose,
    User,
    UserSession,
    UserStatus,
)
from app.core.timeutil import utcnow

_UNIQUE_FIELDS: Final = {"uq_users_username": "username", "uq_users_email": "email"}


class DuplicateUserError(ValueError):
    """``username`` or ``email`` is already taken (case-insensitively)."""

    def __init__(self, field: str) -> None:
        super().__init__(f"duplicate {field}")
        self.field = field


def _rowcount(result: Any) -> int:
    return result.rowcount if isinstance(result, CursorResult) else 0


def _constraint_name(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "__cause__", None) or exc.orig
    name = getattr(diag, "constraint_name", None)
    return name if isinstance(name, str) else None


# --- Users -------------------------------------------------------------------------


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    email: str,
    role: Role,
    status: UserStatus = UserStatus.INVITED,
    password_hash: str | None = None,
) -> User:
    """Insert a user; raises :class:`DuplicateUserError` on a taken username or email.

    Runs in a savepoint, so the caller's transaction stays usable after a duplicate.
    """
    user = User(
        username=username,
        email=email,
        role=role,
        status=status,
        password_hash=password_hash,
        failed_login_count=0,
    )
    try:
        async with session.begin_nested():
            session.add(user)
    except IntegrityError as exc:
        field = _UNIQUE_FIELDS.get(_constraint_name(exc) or "")
        if field is None:
            raise
        raise DuplicateUserError(field) from None
    await session.refresh(user)
    return user


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    """Case-insensitive (citext)."""
    return (
        await session.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Case-insensitive (citext)."""
    return (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()


async def _update_user(
    session: AsyncSession, user_id: uuid.UUID, now: datetime | None, **values: Any
) -> bool:
    result = await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(updated_at=now or utcnow(), **values)
        .execution_options(synchronize_session="fetch")
    )
    return _rowcount(result) == 1


async def update_user_status(
    session: AsyncSession, user_id: uuid.UUID, status: UserStatus, *, now: datetime | None = None
) -> bool:
    return await _update_user(session, user_id, now, status=status)


async def update_user_role(
    session: AsyncSession, user_id: uuid.UUID, role: Role, *, now: datetime | None = None
) -> bool:
    return await _update_user(session, user_id, now, role=role)


async def set_password_hash(
    session: AsyncSession, user_id: uuid.UUID, password_hash: str, *, now: datetime | None = None
) -> bool:
    return await _update_user(session, user_id, now, password_hash=password_hash)


async def increment_failed_logins(
    session: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> int | None:
    """Add one failed attempt; returns the new count (``None`` if no such user)."""
    result = await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(failed_login_count=User.failed_login_count + 1, updated_at=now or utcnow())
        .returning(User.failed_login_count)
        .execution_options(synchronize_session="fetch")
    )
    return result.scalar_one_or_none()


async def set_locked_until(
    session: AsyncSession,
    user_id: uuid.UUID,
    locked_until: datetime | None,
    *,
    now: datetime | None = None,
) -> bool:
    return await _update_user(session, user_id, now, locked_until=locked_until)


async def record_successful_login(
    session: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> bool:
    """Reset the failure counter and lock, and stamp ``last_login_at``."""
    now = now or utcnow()
    return await _update_user(
        session, user_id, now, failed_login_count=0, locked_until=None, last_login_at=now
    )


# --- Sessions ----------------------------------------------------------------------


async def create_session(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    token_hash: bytes,
    expires_at: datetime,
    ip: str | IPv4Address | IPv6Address | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> UserSession:
    """Insert a session. ``ip`` is validated (``ValueError`` if it isn't an address)."""
    now = now or utcnow()
    row = UserSession(
        user_id=user_id,
        token_hash=token_hash,
        created_at=now,
        last_seen_at=now,
        expires_at=expires_at,
        ip=ip_address(ip) if isinstance(ip, str) else ip,
        user_agent=user_agent,
    )
    session.add(row)
    await session.flush()
    return row


async def get_session_by_hash(session: AsyncSession, token_hash: bytes) -> UserSession | None:
    """The session for this token hash, revoked or expired ones included.

    Check :meth:`UserSession.is_active` (and the idle timeout) before trusting it.
    """
    return (
        await session.execute(select(UserSession).where(UserSession.token_hash == token_hash))
    ).scalar_one_or_none()


async def touch_session(
    session: AsyncSession, session_id: uuid.UUID, *, now: datetime | None = None
) -> bool:
    """Update ``last_seen_at`` of an unrevoked session; False if revoked or missing."""
    result = await session.execute(
        update(UserSession)
        .where(UserSession.id == session_id, UserSession.revoked_at.is_(None))
        .values(last_seen_at=now or utcnow())
        .execution_options(synchronize_session="fetch")
    )
    return _rowcount(result) == 1


async def revoke_session(
    session: AsyncSession, session_id: uuid.UUID, *, now: datetime | None = None
) -> bool:
    """Revoke one session; False if it was already revoked or doesn't exist."""
    result = await session.execute(
        update(UserSession)
        .where(UserSession.id == session_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now or utcnow())
        .execution_options(synchronize_session="fetch")
    )
    return _rowcount(result) == 1


async def revoke_all_sessions(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    except_session_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> int:
    """Revoke every unrevoked session of ``user_id`` (optionally keeping one); returns count."""
    stmt = update(UserSession).where(
        UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
    )
    if except_session_id is not None:
        stmt = stmt.where(UserSession.id != except_session_id)
    result = await session.execute(
        stmt.values(revoked_at=now or utcnow()).execution_options(synchronize_session="fetch")
    )
    return _rowcount(result)


# --- Set-password tokens ------------------------------------------------------------


async def create_password_token(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    token_hash: bytes,
    purpose: TokenPurpose,
    expires_at: datetime,
    created_by: uuid.UUID | None = None,
    now: datetime | None = None,
) -> PasswordToken:
    row = PasswordToken(
        user_id=user_id,
        token_hash=token_hash,
        purpose=purpose,
        created_at=now or utcnow(),
        expires_at=expires_at,
        created_by=created_by,
    )
    session.add(row)
    await session.flush()
    return row


async def get_valid_password_token(
    session: AsyncSession,
    token_hash: bytes,
    *,
    purpose: TokenPurpose | None = None,
    now: datetime | None = None,
) -> PasswordToken | None:
    """The token if it is unused and unexpired (and of ``purpose``, when given)."""
    stmt = select(PasswordToken).where(
        PasswordToken.token_hash == token_hash,
        PasswordToken.used_at.is_(None),
        PasswordToken.expires_at > (now or utcnow()),
    )
    if purpose is not None:
        stmt = stmt.where(PasswordToken.purpose == purpose)
    return (await session.execute(stmt)).scalar_one_or_none()


async def mark_password_token_used(
    session: AsyncSession, token_id: uuid.UUID, *, now: datetime | None = None
) -> bool:
    """Mark a token used; False if it was already used, so redemption is single-use
    even under concurrent requests."""
    result = await session.execute(
        update(PasswordToken)
        .where(PasswordToken.id == token_id, PasswordToken.used_at.is_(None))
        .values(used_at=now or utcnow())
        .execution_options(synchronize_session="fetch")
    )
    return _rowcount(result) == 1


async def invalidate_password_tokens(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    purpose: TokenPurpose | None = None,
    now: datetime | None = None,
) -> int:
    """Expire every still-valid token of ``user_id`` (e.g. on re-issue); returns count.

    ``used_at`` is left alone, so it keeps meaning "redeemed".
    """
    now = now or utcnow()
    stmt = update(PasswordToken).where(
        PasswordToken.user_id == user_id,
        PasswordToken.used_at.is_(None),
        PasswordToken.expires_at > now,
    )
    if purpose is not None:
        stmt = stmt.where(PasswordToken.purpose == purpose)
    result = await session.execute(
        stmt.values(expires_at=now).execution_options(synchronize_session="fetch")
    )
    return _rowcount(result)
