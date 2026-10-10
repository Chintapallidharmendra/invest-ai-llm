"""Chat audit events (ADR-032): IDs only, never a title or message text."""

import uuid
from typing import ClassVar

from app.audit.types import AuditEvent


class ConversationCreated(AuditEvent):
    event_type: ClassVar[str] = "chat.conversation_created"

    conversation_id: uuid.UUID
    space_id: uuid.UUID


class ConversationDeleted(AuditEvent):
    """Permanent: the conversation's key was shredded and its rows deleted."""

    event_type: ClassVar[str] = "chat.conversation_deleted"

    conversation_id: uuid.UUID
    space_id: uuid.UUID
