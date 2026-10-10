"""Request and response bodies of the admin endpoints. Metadata only: no admin
response ever carries content, titles or file names (FR-005)."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.auth.models import Role, TokenPurpose, UserStatus

# Letters, digits, dot, underscore and hyphen; matched after NFKC and trimming.
USERNAME_PATTERN = r"^[\w.\-]{1,64}$"
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    username: Annotated[str, Field(pattern=USERNAME_PATTERN)]
    email: Annotated[str, Field(min_length=3, max_length=254, pattern=EMAIL_PATTERN)]
    role: Role


class UpdateUserRequest(BaseModel):
    """At least one of ``role``, ``status`` or ``unlock: true``."""

    model_config = ConfigDict(extra="forbid")

    role: Role | None = None
    status: Literal[UserStatus.ACTIVE, UserStatus.DEACTIVATED] | None = None
    unlock: bool = False

    @model_validator(mode="after")
    def _something_to_change(self) -> "UpdateUserRequest":
        if self.role is None and self.status is None and not self.unlock:
            raise ValueError("nothing to change")
        return self


class AdminUser(BaseModel):
    id: uuid.UUID
    username: str
    email: str
    role: Role
    status: UserStatus
    locked: bool  # failed sign-ins have locked the account for now
    last_login_at: datetime | None
    created_at: datetime


class AdminUserList(BaseModel):
    items: list[AdminUser]
    next_cursor: str | None = None  # the user list is small and unpaginated


class SetPasswordLink(BaseModel):
    """Shown once: the token in ``url`` is never stored, so it can't be shown again."""

    url: str
    purpose: TokenPurpose
    expires_at: datetime


class CreatedUser(BaseModel):
    user: AdminUser
    link: SetPasswordLink


class SpaceCounts(BaseModel):
    private_spaces: int
    owned_workspaces: int
    sole_owner_workspaces: int
    member_workspaces: int


class UpdatedUser(BaseModel):
    user: AdminUser
    # Prompt the admin to transfer or delete these spaces when deactivating (FR-003).
    spaces: SpaceCounts


class SessionRevocation(BaseModel):
    revoked: int
