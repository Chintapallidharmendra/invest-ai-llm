"""Request and response bodies of the conversation endpoints."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

MAX_SELECTED_DOCUMENTS = 200


class CreateConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: uuid.UUID


class RenameConversationRequest(BaseModel):
    """``title`` is whitespace-normalised; 1-200 characters after that."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=1000)]


class SetDocumentsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ids: Annotated[list[uuid.UUID], Field(max_length=MAX_SELECTED_DOCUMENTS)]


class ConversationOut(BaseModel):
    id: uuid.UUID
    space_id: uuid.UUID
    title: str | None  # None until named (by the user or the first turn)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime  # deleted automatically after this (FR-052)


class ConversationList(BaseModel):
    items: list[ConversationOut]
    next_cursor: str | None


class ConversationDetailOut(ConversationOut):
    document_ids: list[uuid.UUID]


class SelectedDocuments(BaseModel):
    document_ids: list[uuid.UUID]
