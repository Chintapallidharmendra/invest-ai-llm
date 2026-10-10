"""Response bodies of the spaces endpoints."""

import uuid

from pydantic import BaseModel

from app.spaces.models import SpaceKind, SpaceRole


class SpaceOut(BaseModel):
    id: uuid.UUID
    kind: SpaceKind
    my_role: SpaceRole
    code_name: str | None  # workspaces only; members only ever see it


class SpaceList(BaseModel):
    items: list[SpaceOut]
    next_cursor: str | None = None  # the list is small and unpaginated
