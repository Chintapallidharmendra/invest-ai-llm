"""Output audit events (ADR-032): metadata only, never a title, file name or content.

The output is also the event's ``target_id``.
"""

import uuid
from datetime import datetime
from typing import ClassVar

from app.audit.types import AuditEvent, KeyedHash
from app.outputs.models import OutputKind, OutputOrigin


class OutputCreated(AuditEvent):
    event_type: ClassVar[str] = "outputs.created"

    output_id: uuid.UUID
    space_id: uuid.UUID
    kind: OutputKind
    origin: OutputOrigin
    size_bytes: int


class DownloadLinkIssued(AuditEvent):
    event_type: ClassVar[str] = "outputs.download_link_issued"

    output_id: uuid.UUID
    expires_at: datetime


class OutputDownloaded(AuditEvent):
    """FR-024: type, size, hash and user (the actor) of every download."""

    event_type: ClassVar[str] = "outputs.downloaded"

    output_id: uuid.UUID
    kind: OutputKind
    size_bytes: int  # of the stored file, before the watermark
    file_digest: KeyedHash  # of the stored file's SHA-256 (a plain hash allows guessing)


class OutputDeleted(AuditEvent):
    event_type: ClassVar[str] = "outputs.deleted"

    output_id: uuid.UUID
    space_id: uuid.UUID
