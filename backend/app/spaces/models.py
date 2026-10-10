"""``spaces`` and ``space_members`` (ADR-023).

Every content row belongs to a space. A user has exactly one private space; workspaces
have members with ``owner``/``member`` roles. Both tables have RLS (see
:mod:`app.spaces.rls`); a workspace's code name is encrypted under the object key
``("space", space_id)``.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

DEFAULT_RETENTION_DAYS = 30  # FR-052; admins configure 7-90 later


class SpaceKind(StrEnum):
    PRIVATE = "private"
    WORKSPACE = "workspace"


class SpaceRole(StrEnum):
    OWNER = "owner"
    MEMBER = "member"


def _pg_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        create_type=False,
    )


space_kind = _pg_enum(SpaceKind, "space_kind")
space_role = _pg_enum(SpaceRole, "space_role")


class Space(Base):
    __tablename__ = "spaces"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    kind: Mapped[SpaceKind] = mapped_column(space_kind)
    # RESTRICT: a user's spaces go through the deletion pipeline before the user does.
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    code_name_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    retention_days: Mapped[int] = mapped_column(
        Integer, server_default=text(str(DEFAULT_RETENTION_DAYS))
    )
    retention_extended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (
        # One private space per owner.
        Index(
            "uq_spaces_private_owner",
            "owner_user_id",
            unique=True,
            postgresql_where=text("kind = 'private'"),
        ),
        Index("ix_spaces_owner_user_id", "owner_user_id"),
        # Private spaces have no code name. A workspace gets one right after creation
        # (its key needs the space row first), so the column may be NULL briefly.
        CheckConstraint(
            "kind = 'workspace' OR code_name_enc IS NULL", name="no_code_name_when_private"
        ),
        CheckConstraint("retention_days BETWEEN 1 AND 3650", name="retention_days_range"),
    )


class SpaceMember(Base):
    __tablename__ = "space_members"

    space_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[SpaceRole] = mapped_column(space_role)
    added_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (Index("ix_space_members_user_id", "user_id"),)
