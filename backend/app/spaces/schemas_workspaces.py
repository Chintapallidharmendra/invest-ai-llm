"""Request and response bodies of the workspace endpoints."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.spaces.models import SpaceRole


class CodeNameRequest(BaseModel):
    """``code_name`` is trimmed; 1-80 characters after trimming."""

    model_config = ConfigDict(extra="forbid")

    # The service applies the exact rule; this only bounds the input.
    code_name: Annotated[str, Field(min_length=1, max_length=400)]


class AddMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Annotated[str, Field(min_length=1, max_length=200)]
    role: SpaceRole = SpaceRole.MEMBER


class CloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: Literal[True]  # closing leads to permanent deletion


class MemberOut(BaseModel):
    user_id: uuid.UUID
    username: str
    role: SpaceRole
    added_at: datetime


class MemberList(BaseModel):
    items: list[MemberOut]
    next_cursor: str | None = None  # small and unpaginated
