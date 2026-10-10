"""One-time set-password links (ADR-006): issue and redeem.

A link is ``https://<site>/set-password#t=<token>``: the token sits in the URL fragment,
so it never reaches server logs or ``Referer`` headers. Only ``hash_token(token)`` is
stored; the raw token leaves :func:`issue_token` once, for the caller to show once.

Redeeming needs the token, the username it was issued for and an acceptable new
password. Success, in one transaction: the token is marked used (single-use even under
concurrent requests), the user's other tokens are expired, the password is set, the user
becomes active (failure counter and lock cleared) and **all** sessions are revoked. Every
token or username problem is the same :class:`LinkInvalidError`, so a caller learns
nothing about which part was wrong.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer
from app.auth import passwords, policy, repository
from app.auth.audit_events_links import LinkRedeemed
from app.auth.models import TokenPurpose, User, UserStatus
from app.auth.policy import ReasonCode
from app.auth.service import USER_TARGET, correlation_id, normalise_username
from app.auth.settings import AuthSettings, get_auth_settings
from app.auth.tokens import generate_token, hash_token
from app.core.db import transaction
from app.core.timeutil import utcnow


class LinkInvalidError(Exception):
    """The token is unknown, used, expired, for another username, or the user can't
    use links (deactivated). Deliberately one error for all of these."""


class LinkNotAllowedError(Exception):
    """A link can't be issued for this user (deactivated)."""


class PasswordRejectedError(Exception):
    def __init__(self, reasons: list[ReasonCode]) -> None:
        super().__init__("password rejected")
        self.reasons = reasons


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str  # raw: shown once, never stored or logged
    purpose: TokenPurpose
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Redeemed:
    user_id: uuid.UUID
    purpose: TokenPurpose
    sessions_revoked: int


def purpose_for(user: User) -> TokenPurpose:
    """``invite`` while the user has never set a password, else ``reset``."""
    return TokenPurpose.INVITE if user.status is UserStatus.INVITED else TokenPurpose.RESET


async def issue_token(
    tx: AsyncSession,
    user: User,
    *,
    created_by: uuid.UUID | None,
    now: datetime | None = None,
    settings: AuthSettings | None = None,
) -> IssuedToken:
    """Expire every earlier link of ``user`` and create a new one, in ``tx``."""
    if user.status is UserStatus.DEACTIVATED:
        raise LinkNotAllowedError
    settings = settings or get_auth_settings()
    now = now or utcnow()
    await repository.invalidate_password_tokens(tx, user.id, now=now)
    token = generate_token()
    purpose = purpose_for(user)
    expires_at = now + settings.password_token_ttl
    await repository.create_password_token(
        tx,
        user_id=user.id,
        token_hash=hash_token(token),
        purpose=purpose,
        expires_at=expires_at,
        created_by=created_by,
        now=now,
    )
    return IssuedToken(token, purpose, expires_at)


async def redeem(
    token: str,
    username: str,
    new_password: str,
    *,
    now: datetime | None = None,
    settings: AuthSettings | None = None,
) -> Redeemed:
    """Set the password through a link; raises :class:`PasswordRejectedError` or
    :class:`LinkInvalidError`.

    The policy is checked first, against the username given, so a rejected password
    reveals nothing about the token, and a valid link isn't used up by a weak password.
    """
    settings = settings or get_auth_settings()
    name = normalise_username(username)
    reasons = policy.validate(new_password, name, settings=settings)
    if reasons:
        raise PasswordRejectedError(reasons)
    if not token or not name:
        raise LinkInvalidError
    # Hash outside the transaction: no row locks held during the Argon2 work.
    new_hash = passwords.hash_password(new_password)
    now = now or utcnow()
    async with transaction() as tx:
        row = await repository.get_valid_password_token(tx, hash_token(token), now=now)
        user = await repository.get_user_by_username(tx, name)
        if (
            row is None
            or user is None
            or user.id != row.user_id
            or user.status is UserStatus.DEACTIVATED
        ):
            raise LinkInvalidError
        if not await repository.mark_password_token_used(tx, row.id, now=now):
            raise LinkInvalidError  # a concurrent request redeemed it first
        await repository.invalidate_password_tokens(tx, user.id, now=now)
        await repository.set_password_hash(tx, user.id, new_hash, now=now)
        await _activate(tx, user.id, now)
        revoked = await repository.revoke_all_sessions(tx, user.id, now=now)
        await writer.record(
            LinkRedeemed(purpose=row.purpose, sessions_revoked=revoked),
            actor_user_id=user.id,
            correlation_id=correlation_id(),
            target_type=USER_TARGET,
            target_id=user.id,
            session=tx,
        )
        return Redeemed(user.id, row.purpose, revoked)


async def _activate(tx: AsyncSession, user_id: uuid.UUID, now: datetime) -> None:
    """Active, with the failure counter and any lock cleared (a reset also unlocks)."""
    await tx.execute(
        update(User)
        .where(User.id == user_id)
        .values(status=UserStatus.ACTIVE, failed_login_count=0, locked_until=None, updated_at=now)
        .execution_options(synchronize_session="fetch")
    )
