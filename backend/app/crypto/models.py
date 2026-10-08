"""``space_keys`` and ``object_keys``: wrapped keys only, never key material in clear.

No RLS (the rows hold no content) and no FK to ``spaces`` yet (Story 8.2 adds it). Access
goes through :mod:`app.crypto.keys`. ``app_rw`` may SELECT, INSERT and DELETE (deleting
a row is crypto-shredding); only rotation, as ``app_migrator``, UPDATEs.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, LargeBinary, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# version(1) | nonce(12) | 32-byte key | tag(16)
WRAPPED_KEY_BYTES = 61


class SpaceKey(Base):
    __tablename__ = "space_keys"
    __table_args__ = (
        CheckConstraint(
            f"octet_length(wrapped_key) = {WRAPPED_KEY_BYTES}", name="wrapped_key_length"
        ),
        CheckConstraint("kek_version >= 1", name="kek_version_positive"),
    )

    space_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary)
    kek_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ObjectKey(Base):
    __tablename__ = "object_keys"
    __table_args__ = (
        CheckConstraint(
            f"octet_length(wrapped_key) = {WRAPPED_KEY_BYTES}", name="wrapped_key_length"
        ),
        CheckConstraint(r"object_type ~ '^[a-z][a-z0-9_]{0,31}$'", name="object_type"),
        Index("ix_object_keys_space_id", "space_id"),
    )

    object_type: Mapped[str] = mapped_column(Text, primary_key=True)
    object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    space_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
