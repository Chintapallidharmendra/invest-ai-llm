"""Hash-chain primitives (ADR-016 as kept by ADR-032).

``hash = SHA-256(prev_hash || canonical_json(row without hash))``, where the row's JSON
holds every stored column (``prev_hash`` as hex) and canonical JSON means sorted keys,
no whitespace, ASCII only. Values are normalised so a row read back from Postgres
serialises to exactly the bytes hashed when it was written.
"""

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Final

from app.audit.types import AuditEvent

HASH_BYTES: Final = 32
ZERO_HASH: Final = bytes(HASH_BYTES)  # prev_hash of the first event ever
# pg_advisory_xact_lock key serialising writers ("audit_v1" as an int64).
WRITER_LOCK_KEY: Final = int.from_bytes(b"audit_v1", "big") & 0x7FFF_FFFF_FFFF_FFFF
VERIFY_LOCK_KEY: Final = WRITER_LOCK_KEY - 1

JsonValue = None | bool | int | str


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def canonical_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("audit timestamps must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def to_json_value(value: Any) -> JsonValue:
    """One payload value as stored: enums by value, durations in microseconds."""
    if value is None or (isinstance(value, bool | int) and not isinstance(value, Enum)):
        return value
    if isinstance(value, Enum):
        inner = value.value
        if not isinstance(inner, str | int):
            raise TypeError("audit enums must have str or int values")
        return inner
    if isinstance(value, str):  # VersionStr, KeyedHash (validated by the model)
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return canonical_datetime(value)
    if isinstance(value, timedelta):
        return value // timedelta(microseconds=1)
    raise TypeError(f"unsupported audit value type: {type(value).__name__}")


def payload_of(event: AuditEvent) -> dict[str, JsonValue]:
    return {name: to_json_value(getattr(event, name)) for name in type(event).model_fields}


@dataclass(frozen=True, slots=True)
class ChainRow:
    """The stored columns of one audit row (everything but ``hash``)."""

    id: uuid.UUID
    seq: int
    occurred_at: datetime
    correlation_id: uuid.UUID
    actor_user_id: uuid.UUID | None
    event_type: str
    target_type: str | None
    target_id: uuid.UUID | None
    priority: str
    payload: Mapping[str, JsonValue]
    prev_hash: bytes

    def material(self) -> bytes:
        return canonical_json(
            {
                "id": str(self.id),
                "seq": self.seq,
                "occurred_at": canonical_datetime(self.occurred_at),
                "correlation_id": str(self.correlation_id),
                "actor_user_id": str(self.actor_user_id) if self.actor_user_id else None,
                "event_type": self.event_type,
                "target_type": self.target_type,
                "target_id": str(self.target_id) if self.target_id else None,
                "priority": self.priority,
                "payload": dict(self.payload),
                "prev_hash": self.prev_hash.hex(),
            }
        )

    def compute_hash(self) -> bytes:
        return hashlib.sha256(self.prev_hash + self.material()).digest()
