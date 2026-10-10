"""Login, session checks and logout (ADR-006).

Every login attempt does exactly one Argon2 verification, whatever the outcome (an
unknown user verifies against a dummy hash), so response time doesn't reveal whether a
username exists or why an attempt failed. Failures are committed (counter, lock, audit)
before the caller raises, so a rolled-back request never hides an attempt.

Raw tokens leave this module only in :class:`LoginSuccess`, for the cookies; only their
SHA-256 is stored.
"""

import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from ipaddress import ip_address

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer
from app.audit.types import KeyedHash
from app.auth import passwords, repository
from app.auth.audit_events import (
    AccountLocked,
    LoggedOut,
    LoginFailed,
    LoginFailureReason,
    LoginSucceeded,
)
from app.auth.models import Role, User, UserSession, UserStatus
from app.auth.settings import AuthSettings, get_auth_settings
from app.auth.tokens import generate_token, hash_token
from app.core.db import transaction
from app.core.ids import new_uuid7
from app.core.obs import current_correlation_id
from app.core.timeutil import utcnow

USER_TARGET = "user"
SESSION_TARGET = "session"


@dataclass(frozen=True, slots=True)
class SessionUser:
    """The user behind a valid session (what ``auth.deps.current_user`` returns)."""

    id: uuid.UUID
    username: str
    role: Role
    session_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class LoginSuccess:
    user: SessionUser
    session_token: str  # raw: goes into the __Host-session cookie, never stored
    csrf_token: str  # raw: goes into the __Host-csrf cookie


@dataclass(frozen=True, slots=True)
class LoginFailure:
    reason: LoginFailureReason


LoginOutcome = LoginSuccess | LoginFailure


def correlation_id() -> uuid.UUID:
    """The request's correlation ID, or a fresh one outside a request."""
    value = current_correlation_id()
    return uuid.UUID(value) if value else new_uuid7()


def normalise_username(username: str) -> str:
    """NFKC and trimmed; case is handled by the ``citext`` column."""
    return unicodedata.normalize("NFKC", username).strip()


# --- Login -----------------------------------------------------------------------------


async def login(
    username: str,
    password: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    previous_token: str | None = None,
    now: datetime | None = None,
    settings: AuthSettings | None = None,
) -> LoginOutcome:
    """Check the credentials and, on success, open a session (rotating any old one)."""
    settings = settings or get_auth_settings()
    now = now or utcnow()
    name = normalise_username(username)
    async with transaction() as tx:
        user = await repository.get_user_by_username(tx, name) if name else None
        if len(password) > settings.password_max_length:
            # Refused before hashing: no Argon2 work for an oversized input.
            return await _fail(tx, user, name, LoginFailureReason.TOO_LONG, now, settings)
        if user is None or user.password_hash is None:
            passwords.verify_dummy(password)
            reason = LoginFailureReason.UNKNOWN_USER if user is None else _blocked(user, now)
            return await _fail(tx, user, name, reason or LoginFailureReason.INVITED, now, settings)

        verified, new_hash = passwords.verify_and_rehash(password, user.password_hash)
        blocked = _blocked(user, now)
        if blocked is not None:
            return await _fail(tx, user, name, blocked, now, settings, count=False)
        if not verified:
            return await _fail(tx, user, name, LoginFailureReason.WRONG_PASSWORD, now, settings)
        return await _succeed(
            tx,
            user,
            new_hash,
            ip=ip,
            user_agent=user_agent,
            previous_token=previous_token,
            now=now,
            settings=settings,
        )


def _blocked(user: User, now: datetime) -> LoginFailureReason | None:
    """Why a user may not log in even with the right password, if anything."""
    if user.status is UserStatus.DEACTIVATED:
        return LoginFailureReason.DEACTIVATED
    if user.status is UserStatus.INVITED:
        return LoginFailureReason.INVITED
    if user.status is UserStatus.LOCKED or (
        user.locked_until is not None and user.locked_until > now
    ):
        return LoginFailureReason.LOCKED
    return None


async def _fail(  # noqa: PLR0917 (private helper, called positionally in one place)
    tx: AsyncSession,
    user: User | None,
    name: str,
    reason: LoginFailureReason,
    now: datetime,
    settings: AuthSettings,
    *,
    count: bool = True,
) -> LoginFailure:
    if user is None:
        await writer.record(
            LoginFailed(reason=reason, username_hash=KeyedHash.of(name.casefold())),
            actor_user_id=None,
            correlation_id=correlation_id(),
            session=tx,
        )
        return LoginFailure(reason)

    failed_count: int | None = None
    counts = count and reason in {LoginFailureReason.WRONG_PASSWORD, LoginFailureReason.TOO_LONG}
    if counts and user.status is UserStatus.ACTIVE:
        failed_count = await _count_failure(tx, user, now)
    await writer.record(
        LoginFailed(reason=reason, failed_count=failed_count),
        actor_user_id=user.id,
        correlation_id=correlation_id(),
        target_type=USER_TARGET,
        target_id=user.id,
        session=tx,
    )
    if failed_count is not None and failed_count >= settings.login_max_failures:
        locked_until = now + settings.lockout
        await repository.set_locked_until(tx, user.id, locked_until, now=now)
        await writer.record(
            AccountLocked(locked_until=locked_until, failed_count=failed_count),
            actor_user_id=user.id,
            correlation_id=correlation_id(),
            target_type=USER_TARGET,
            target_id=user.id,
            session=tx,
        )
    return LoginFailure(reason)


async def _count_failure(tx: AsyncSession, user: User, now: datetime) -> int:
    if user.locked_until is not None and user.locked_until <= now:
        # The previous lock has expired: this failure starts a new run of five.
        await tx.execute(
            update(User)
            .where(User.id == user.id)
            .values(failed_login_count=1, locked_until=None, updated_at=now)
            .execution_options(synchronize_session="fetch")
        )
        return 1
    count = await repository.increment_failed_logins(tx, user.id, now=now)
    return count or 0


async def _succeed(
    tx: AsyncSession,
    user: User,
    new_hash: str | None,
    *,
    ip: str | None,
    user_agent: str | None,
    previous_token: str | None,
    now: datetime,
    settings: AuthSettings,
) -> LoginSuccess:
    if new_hash is not None:
        await repository.set_password_hash(tx, user.id, new_hash, now=now)
    await repository.record_successful_login(tx, user.id, now=now)

    rotated = False
    if previous_token:
        old = await repository.get_session_by_hash(tx, hash_token(previous_token))
        if old is not None:
            rotated = await repository.revoke_session(tx, old.id, now=now)

    token = generate_token()
    row = await repository.create_session(
        tx,
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=now + settings.session_absolute_ttl,
        ip=_valid_ip(ip),
        user_agent=(user_agent or None) and user_agent[:512],
        now=now,
    )
    await writer.record(
        LoginSucceeded(session_id=row.id, rehashed=new_hash is not None, rotated_session=rotated),
        actor_user_id=user.id,
        correlation_id=correlation_id(),
        target_type=SESSION_TARGET,
        target_id=row.id,
        session=tx,
    )
    return LoginSuccess(
        user=SessionUser(user.id, user.username, user.role, row.id),
        session_token=token,
        csrf_token=generate_token(),
    )


def _valid_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    try:
        return str(ip_address(ip))
    except ValueError:
        return None  # e.g. "testclient": not an address, so not recorded


# --- Session checks ---------------------------------------------------------------------


async def authenticate(
    token: str | None,
    *,
    now: datetime | None = None,
    settings: AuthSettings | None = None,
) -> SessionUser | None:
    """The user of a valid session token, else ``None``.

    Valid means: not revoked, before the absolute expiry, seen within the idle timeout,
    and the user is active. ``last_seen_at`` is written at most once per
    ``session_touch_interval_s``.
    """
    if not token:
        return None
    settings = settings or get_auth_settings()
    now = now or utcnow()
    async with transaction() as tx:
        row = (
            await tx.execute(
                select(UserSession, User)
                .join(User, User.id == UserSession.user_id)
                .where(UserSession.token_hash == hash_token(token))
            )
        ).first()
        if row is None:
            return None
        session, user = row
        if not session.is_active(now) or user.status is not UserStatus.ACTIVE:
            return None
        idle = now - session.last_seen_at
        if idle > settings.session_idle_timeout:
            return None
        if idle.total_seconds() >= settings.session_touch_interval_s:
            await repository.touch_session(tx, session.id, now=now)
        return SessionUser(user.id, user.username, user.role, session.id)


# --- Logout -------------------------------------------------------------------------------


async def logout(user: SessionUser, *, now: datetime | None = None) -> bool:
    """Revoke the caller's session; True if it was still active."""
    async with transaction() as tx:
        revoked = await repository.revoke_session(tx, user.session_id, now=now)
        await writer.record(
            LoggedOut(session_id=user.session_id),
            actor_user_id=user.id,
            correlation_id=correlation_id(),
            target_type=SESSION_TARGET,
            target_id=user.session_id,
            session=tx,
        )
    return revoked
