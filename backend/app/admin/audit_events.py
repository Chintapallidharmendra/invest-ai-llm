"""Admin audit events (ADR-032): IDs and enums only; the target user is ``target_id``.

Never a username, email, link or token.
"""

from datetime import datetime
from typing import ClassVar

from app.audit.types import AuditEvent
from app.auth.models import Role, TokenPurpose, UserStatus


class UserCreated(AuditEvent):
    event_type: ClassVar[str] = "admin.user_created"

    role: Role
    bootstrap: bool = False  # the first admin, created from the command line


class LinkIssued(AuditEvent):
    event_type: ClassVar[str] = "admin.link_issued"

    purpose: TokenPurpose
    expires_at: datetime


class UserUpdated(AuditEvent):
    """What changed: each field is set only if that part changed."""

    event_type: ClassVar[str] = "admin.user_updated"

    new_role: Role | None = None
    new_status: UserStatus | None = None
    unlocked: bool = False
    sessions_revoked: int = 0


class SessionsRevoked(AuditEvent):
    event_type: ClassVar[str] = "admin.sessions_revoked"

    count: int
