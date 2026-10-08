"""``audit_events`` (partitioned monthly by ``occurred_at``) and ``audit_anchors``.

Append-only: ``app_rw`` may only INSERT and SELECT ``audit_events`` and only SELECT
``audit_anchors``, and a trigger rejects UPDATE and DELETE on both (migration
``7_1_audit``). Monthly partitions ``audit_events_yYYYYmMM`` are created by the
``audit_ensure_partitions`` SQL function, never by Alembic.

Postgres cannot enforce a unique constraint across partitions without the partition
key, so uniqueness is ``(seq, occurred_at)``; ``seq`` itself is gap-free and unique by
construction (one writer at a time under an advisory lock) and checked by verification.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.audit.types import AuditPriority
from app.core.db import Base

audit_priority = Enum(
    AuditPriority,
    name="audit_priority",
    values_callable=lambda e: [m.value for m in e],
    create_type=False,
)


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("seq", "occurred_at", name="uq_audit_events_seq"),
        Index("ix_audit_events_actor_user_id_occurred_at", "actor_user_id", "occurred_at"),
        Index("ix_audit_events_event_type_occurred_at", "event_type", "occurred_at"),
        CheckConstraint("seq > 0", name="seq_positive"),
        CheckConstraint("octet_length(prev_hash) = 32", name="prev_hash_length"),
        CheckConstraint("octet_length(hash) = 32", name="hash_length"),
        CheckConstraint(r"event_type ~ '^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$'", name="event_type"),
        CheckConstraint(r"target_type ~ '^[a-z][a-z0-9_]{0,31}$'", name="target_type"),
        {"postgresql_partition_by": "RANGE (occurred_at)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    event_type: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    priority: Mapped[AuditPriority] = mapped_column(
        audit_priority, server_default=AuditPriority.NORMAL.value
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    prev_hash: Mapped[bytes] = mapped_column(LargeBinary)
    hash: Mapped[bytes] = mapped_column(LargeBinary)


class AuditAnchor(Base):
    """The chain head as it stood when a partition was dropped (retention)."""

    __tablename__ = "audit_anchors"
    __table_args__ = (
        CheckConstraint("last_seq >= 0", name="last_seq_non_negative"),
        CheckConstraint("octet_length(last_hash) = 32", name="last_hash_length"),
    )

    partition_name: Mapped[str] = mapped_column(Text, primary_key=True)
    last_seq: Mapped[int] = mapped_column(BigInteger)
    last_hash: Mapped[bytes] = mapped_column(LargeBinary)
    dropped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
