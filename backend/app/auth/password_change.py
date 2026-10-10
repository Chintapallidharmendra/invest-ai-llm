"""Change your own password while signed in (FR-038, ADR-006).

The current password must be verified. A wrong one counts toward the lockout exactly
like a failed login (same counter, threshold and lock), and the failure is committed
before the caller turns it into a 400. While the account is locked, the check always
fails and nothing more is counted.

The new password goes through the policy, and must differ from the current one (after
NFKC normalisation, which the hash verification applies). On success, in one
transaction: the new hash is set, every *other* session of the user is revoked, the
failure counter is cleared and ``auth.password_changed`` is recorded.

The user row is locked for the whole check, so concurrent change requests run one after
the other: the second is checked against whatever the first set.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer
from app.auth import passwords, policy, repository
from app.auth.audit_events import AccountLocked
from app.auth.audit_events_password import PasswordChanged, PasswordChangeFailed
from app.auth.models import User
from app.auth.policy import ReasonCode
from app.auth.service import USER_TARGET, SessionUser, correlation_id
from app.auth.settings import AuthSettings, get_auth_settings
from app.core.db import transaction
from app.core.timeutil import utcnow


class ChangeFailure(StrEnum):
    CURRENT_PASSWORD_INVALID = "current_password_invalid"  # noqa: S105 (a failure code)
    PASSWORD_REJECTED = "password_rejected"  # noqa: S105
    PASSWORD_UNCHANGED = "password_unchanged"  # noqa: S105


@dataclass(frozen=True, slots=True)
class Changed:
    sessions_revoked: int


@dataclass(frozen=True, slots=True)
class Rejected:
    failure: ChangeFailure
    reasons: tuple[ReasonCode, ...] = ()


async def change_password(
    user: SessionUser,
    current_password: str,
    new_password: str,
    *,
    now: datetime | None = None,
    settings: AuthSettings | None = None,
) -> Changed | Rejected:
    settings = settings or get_auth_settings()
    now = now or utcnow()
    async with transaction() as tx:
        row = (
            await tx.execute(
                select(User)
                .where(User.id == user.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        locked = row.locked_until is not None and row.locked_until > now
        verified = (
            row.password_hash is not None
            and len(current_password) <= settings.password_max_length
            and passwords.verify_password(current_password, row.password_hash)
        )
        if locked or not verified:
            await _count_failure(tx, row, now, settings, locked=locked)
            return Rejected(ChangeFailure.CURRENT_PASSWORD_INVALID)

        reasons = policy.validate(new_password, row.username, settings=settings)
        if reasons:
            return Rejected(ChangeFailure.PASSWORD_REJECTED, tuple(reasons))
        assert row.password_hash is not None  # noqa: S101 (verified above)
        if passwords.verify_password(new_password, row.password_hash):
            return Rejected(ChangeFailure.PASSWORD_UNCHANGED)

        await tx.execute(
            update(User)
            .where(User.id == user.id)
            .values(
                password_hash=passwords.hash_password(new_password),
                failed_login_count=0,
                locked_until=None,
                updated_at=now,
            )
            .execution_options(synchronize_session="fetch")
        )
        revoked = await repository.revoke_all_sessions(
            tx, user.id, except_session_id=user.session_id, now=now
        )
        await _record(tx, user.id, PasswordChanged(sessions_revoked=revoked))
    return Changed(revoked)


async def _count_failure(
    tx: AsyncSession, row: User, now: datetime, settings: AuthSettings, *, locked: bool
) -> None:
    """One more consecutive failure, and the lock at the threshold (as for logins)."""
    if locked:
        await _record(tx, row.id, PasswordChangeFailed(failed_count=None))
        return
    if row.locked_until is not None:
        # The previous lock has expired: this failure starts a new run.
        await tx.execute(
            update(User)
            .where(User.id == row.id)
            .values(failed_login_count=1, locked_until=None, updated_at=now)
            .execution_options(synchronize_session="fetch")
        )
        count = 1
    else:
        count = await repository.increment_failed_logins(tx, row.id, now=now) or 0
    await _record(tx, row.id, PasswordChangeFailed(failed_count=count))
    if count >= settings.login_max_failures:
        locked_until = now + settings.lockout
        await repository.set_locked_until(tx, row.id, locked_until, now=now)
        await _record(tx, row.id, AccountLocked(locked_until=locked_until, failed_count=count))


async def _record(
    tx: AsyncSession,
    user_id: uuid.UUID,
    event: PasswordChanged | PasswordChangeFailed | AccountLocked,
) -> None:
    await writer.record(
        event,
        actor_user_id=user_id,
        correlation_id=correlation_id(),
        target_type=USER_TARGET,
        target_id=user_id,
        session=tx,
    )
