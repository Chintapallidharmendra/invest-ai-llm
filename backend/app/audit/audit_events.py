"""The audit module's own events (verification and retention)."""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from app.audit.types import AuditEvent


class VerifyMode(StrEnum):
    INCREMENTAL = "incremental"  # rows since the last verified seq
    FULL = "full"  # the whole chain, from the latest anchor (or the first event)


class MismatchReason(StrEnum):
    GAP = "gap"  # a seq is missing or out of order
    PREV_HASH = "prev_hash"  # prev_hash isn't the previous row's hash (or the anchor's)
    HASH = "hash"  # the stored hash doesn't match the row's content


class AuditVerified(AuditEvent):
    event_type: ClassVar[str] = "audit.verified"

    mode: VerifyMode
    from_seq: int  # rows after this seq were checked
    through_seq: int
    row_count: int


class AuditVerifyFailed(AuditEvent):
    event_type: ClassVar[str] = "audit.verify_failed"

    mode: VerifyMode
    seq: int  # the first row that failed
    reason: MismatchReason


class AuditPartitionDropped(AuditEvent):
    event_type: ClassVar[str] = "audit.partition_dropped"

    partition_month: datetime  # first instant (UTC) of the dropped month
    last_seq: int  # the anchor written before the drop
