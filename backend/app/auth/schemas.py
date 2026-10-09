"""Request and response bodies of the auth endpoints."""

import uuid

from pydantic import BaseModel, ConfigDict

from app.auth.models import Role


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # No length limits here: an oversized password is refused as invalid credentials
    # (before hashing), so the response is the same as for any other failure.
    username: str
    password: str


class Me(BaseModel):
    """The signed-in user; the frontend's ``CurrentUser``."""

    id: uuid.UUID
    username: str
    role: Role
