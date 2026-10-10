"""Generated files and their download links (ADR-023, ADR-028): ``outputs``,
``download_tokens``.

Both tables have owner-only space RLS: an output is visible only to its creator, and
only while they are still a member of its space (FR-040: a removed member loses access
to outputs they generated from workspace documents). A download token belongs to the
output's owner; a composite foreign key makes its ``(space_id, user_id)`` equal to the
output's ``(space_id, owner_user_id)``.

The file itself is encrypted on disk (``file_path``) under the object key
``("output", id)``; the title and operations log are encrypted fields.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class OutputKind(StrEnum):
    XLSX = "xlsx"
    CSV = "csv"
    DOCX = "docx"


class OutputOrigin(StrEnum):
    OPERATIONS = "operations"  # sheet operations (10.6)
    TABLE_EXTRACT = "table_extract"  # tables extracted from documents (9.11)
    SUMMARY = "summary"  # document summaries (9.13)
    COMPARISON = "comparison"  # document comparisons (9.12)


def _pg_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        create_type=False,
    )


output_kind = _pg_enum(OutputKind, "output_kind")
output_origin = _pg_enum(OutputOrigin, "output_origin")


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=text("now()"))


class Output(Base):
    __tablename__ = "outputs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    space_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE")
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    kind: Mapped[OutputKind] = mapped_column(output_kind)
    origin: Mapped[OutputOrigin] = mapped_column(output_origin)
    # FK to documents is added with the documents table (9.1).
    source_document_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default=text("'{}'::uuid[]")
    )
    version_of_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("outputs.id", ondelete="SET NULL")
    )
    title_enc: Mapped[bytes] = mapped_column(LargeBinary)
    # Relative to APP_OUTPUTS_FILES_DIR: "<space_id>/<output_id>".
    file_path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)  # plaintext size
    # KeyedHash (audit key) of the plaintext's SHA-256, for the download audit (ADR-032).
    content_hash: Mapped[str] = mapped_column(Text)
    ops_log_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (
        UniqueConstraint("id", "space_id", "owner_user_id", name="uq_outputs_id_space_owner"),
        Index("ix_outputs_owner_space_created", "owner_user_id", "space_id", "created_at"),
        Index("ix_outputs_version_of_id", "version_of_id"),
        Index("ix_outputs_expires_at", "expires_at"),
        CheckConstraint("size_bytes >= 0", name="size_non_negative"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_format"),
        CheckConstraint("file_path ~ '^[0-9a-f-]{36}/[0-9a-f-]{36}$'", name="file_path_format"),
    )


class DownloadToken(Base):
    __tablename__ = "download_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    output_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    space_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)  # SHA-256
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        ForeignKeyConstraint(
            ["output_id", "space_id", "user_id"],
            ["outputs.id", "outputs.space_id", "outputs.owner_user_id"],
            ondelete="CASCADE",
        ),
        Index("ix_download_tokens_output_id", "output_id"),
        CheckConstraint("octet_length(token_hash) = 32", name="token_hash_length"),
        CheckConstraint("used_count >= 0", name="used_count_non_negative"),
    )
