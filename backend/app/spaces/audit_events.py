"""Space audit events (ADR-032): IDs only."""

import uuid
from typing import ClassVar

from app.audit.types import AuditEvent


class PrivateSpaceCreated(AuditEvent):
    event_type: ClassVar[str] = "spaces.private_created"

    space_id: uuid.UUID
    owner_user_id: uuid.UUID
