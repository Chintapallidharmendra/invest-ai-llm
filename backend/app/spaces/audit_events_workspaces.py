"""Workspace audit events (ADR-032): IDs and enums only, never a code name.

The space is also the event's ``target_id``.
"""

import uuid
from typing import ClassVar

from app.audit.types import AuditEvent
from app.spaces.models import SpaceRole


class WorkspaceCreated(AuditEvent):
    event_type: ClassVar[str] = "spaces.workspace_created"

    space_id: uuid.UUID


class WorkspaceRenamed(AuditEvent):
    event_type: ClassVar[str] = "spaces.workspace_renamed"

    space_id: uuid.UUID


class MemberAdded(AuditEvent):
    event_type: ClassVar[str] = "spaces.member_added"

    space_id: uuid.UUID
    user_id: uuid.UUID
    role: SpaceRole


class MemberRemoved(AuditEvent):
    event_type: ClassVar[str] = "spaces.member_removed"

    space_id: uuid.UUID
    user_id: uuid.UUID
    by_self: bool  # the member left, rather than being removed by an owner


class MemberRoleChanged(AuditEvent):
    event_type: ClassVar[str] = "spaces.member_role_changed"

    space_id: uuid.UUID
    user_id: uuid.UUID
    role: SpaceRole  # the new role


class WorkspaceClosed(AuditEvent):
    event_type: ClassVar[str] = "spaces.workspace_closed"

    space_id: uuid.UUID
    deletion_queued: bool  # a deletion-pipeline job was queued (7.4)
