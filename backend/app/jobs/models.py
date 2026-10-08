"""The ``jobs`` table (ADR-017). Platform metadata only: payloads hold IDs and enums,
never content, so the table has no RLS. User-facing reads go through
:func:`app.jobs.service.get_job_for_owner`.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import CheckConstraint, DateTime, Enum, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

DEFAULT_PRIORITY: Final = 100
DEFAULT_MAX_ATTEMPTS: Final = 3


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES: Final = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED})

job_status = Enum(
    JobStatus,
    name="job_status",
    values_callable=lambda e: [m.value for m in e],
    create_type=False,
)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    type: Mapped[str] = mapped_column(Text)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    space_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # Lower runs first.
    priority: Mapped[int] = mapped_column(Integer, server_default=text(str(DEFAULT_PRIORITY)))
    status: Mapped[JobStatus] = mapped_column(job_status, server_default=JobStatus.QUEUED.value)
    progress: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(
        Integer, server_default=text(str(DEFAULT_MAX_ATTEMPTS))
    )
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A fresh token per claim: a runner that lost its lease can't finish the job.
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    error_code: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(Text)
    # Last claim time; also drives round-robin fairness for heavy jobs.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_jobs_status_priority_run_after", "status", "priority", "run_after"),
        Index("ix_jobs_user_id", "user_id"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
    )
