"""Authentication audit events (ADR-032): metadata only.

No password, token or username is ever recorded. An unknown username is kept only as a
:class:`~app.audit.types.KeyedHash`, so compliance can group repeated attempts on one
name without the name being stored.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from app.audit.types import AuditEvent, KeyedHash
from app.auth.models import Role


class LoginFailureReason(StrEnum):
    UNKNOWN_USER = "unknown_user"
    WRONG_PASSWORD = "wrong_password"  # noqa: S105 (a reason code, not a password)
    LOCKED = "locked"
    DEACTIVATED = "deactivated"
    INVITED = "invited"
    TOO_LONG = "too_long"  # refused before hashing


class LoginSucceeded(AuditEvent):
    event_type: ClassVar[str] = "auth.login_succeeded"

    session_id: uuid.UUID
    rehashed: bool  # the stored hash was upgraded to current parameters
    rotated_session: bool  # a session cookie already on the request was revoked


class LoginFailed(AuditEvent):
    event_type: ClassVar[str] = "auth.login_failed"

    reason: LoginFailureReason
    username_hash: KeyedHash | None = None  # unknown users only
    failed_count: int | None = None  # consecutive failures after this one (known users)


class AccountLocked(AuditEvent):
    event_type: ClassVar[str] = "auth.account_locked"

    locked_until: datetime
    failed_count: int


class LoggedOut(AuditEvent):
    event_type: ClassVar[str] = "auth.logged_out"

    session_id: uuid.UUID


class AccessDenied(AuditEvent):
    event_type: ClassVar[str] = "auth.access_denied"

    role: Role  # the caller's role, which the endpoint doesn't allow
