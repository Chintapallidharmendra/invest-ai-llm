"""Response bodies of the output endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.outputs.models import OutputKind, OutputOrigin


class OutputOut(BaseModel):
    id: uuid.UUID
    space_id: uuid.UUID
    kind: OutputKind
    origin: OutputOrigin
    title: str
    size_bytes: int
    source_document_ids: list[uuid.UUID]
    version_of_id: uuid.UUID | None
    created_at: datetime
    expires_at: datetime


class OutputList(BaseModel):
    items: list[OutputOut]
    next_cursor: str | None = None  # a user's outputs are few; unpaginated


class OutputDetailOut(OutputOut):
    versions: list[OutputOut]


class DownloadLink(BaseModel):
    """Shown once: the token in ``url`` is not stored, so it can't be shown again."""

    url: str  # /api/v1/downloads/<token>, valid for the caller only
    expires_at: datetime
