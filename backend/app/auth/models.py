"""Identity tables (ADR-006, ADR-023): ``users``, ``sessions``, ``password_tokens``.

They hold no deal content, so they have no RLS and no field encryption. Session and
set-password tokens are stored only as ``sha256(token)``.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address
from typing import Final

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

TOKEN_HASH_LENGTH: Final = 32  # SHA-256


class Role(StrEnum):
    USER = "user"
    ADMIN = "admin"
    COMPLIANCE = "compliance"


class UserStatus(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    LOCKED = "locked"
    DEACTIVATED = "deactivated"


class TokenPurpose(StrEnum):
    INVITE = "invite"
    RESET = "reset"


def _pg_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        create_type=False,
    )


user_role = _pg_enum(Role, "user_role")
user_status = _pg_enum(UserStatus, "user_status")
password_token_purpose = _pg_enum(TokenPurpose, "password_token_purpose")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()"))


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=text("now()"))


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    username: Mapped[str] = mapped_column(CITEXT, unique=True)
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    role: Mapped[Role] = mapped_column(user_role)
    status: Mapped[UserStatus] = mapped_column(user_status, server_default=UserStatus.INVITED.value)
    password_hash: Mapped[str | None] = mapped_column(Text)
    failed_login_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (
        CheckConstraint("char_length(username) BETWEEN 1 AND 64", name="username_length"),
        CheckConstraint("char_length(email) BETWEEN 3 AND 254", name="email_length"),
        CheckConstraint("failed_login_count >= 0", name="failed_login_count_non_negative"),
        # Only invited users may lack a password.
        CheckConstraint(
            "status = 'invited' OR password_hash IS NOT NULL", name="password_unless_invited"
        ),
    )


class UserSession(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    created_at: Mapped[datetime] = _now()
    last_seen_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[IPv4Address | IPv6Address | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index(
            "ix_sessions_user_id_active",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        CheckConstraint(
            f"octet_length(token_hash) = {TOKEN_HASH_LENGTH}", name="token_hash_length"
        ),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
    )

    def is_active(self, now: datetime) -> bool:
        """Not revoked and before the absolute expiry (idle timeout is the caller's)."""
        return self.revoked_at is None and now < self.expires_at


class PasswordToken(Base):
    __tablename__ = "password_tokens"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    purpose: Mapped[TokenPurpose] = mapped_column(password_token_purpose)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        CheckConstraint(
            f"octet_length(token_hash) = {TOKEN_HASH_LENGTH}", name="token_hash_length"
        ),
    )
