"""Audit events of password changes (ADR-032): metadata only, never a password."""

from typing import ClassVar

from app.audit.types import AuditEvent


class PasswordChanged(AuditEvent):
    event_type: ClassVar[str] = "auth.password_changed"

    sessions_revoked: int  # the user's other sessions, ended by the change


class PasswordChangeFailed(AuditEvent):
    """The current password was wrong (counts toward the lockout like a failed login)."""

    event_type: ClassVar[str] = "auth.password_change_failed"

    failed_count: int | None = None  # consecutive failures after this one; None if locked
