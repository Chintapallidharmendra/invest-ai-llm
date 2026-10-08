"""Dummy audit events for the tests (registered on import, like a module's own)."""

import uuid
from datetime import datetime, timedelta
from enum import IntEnum, StrEnum
from typing import ClassVar

from app.audit.types import AuditEvent, KeyedHash, VersionStr


class Colour(StrEnum):
    RED = "red"
    GREEN = "green"


class Level(IntEnum):
    LOW = 1
    HIGH = 2


class DummyEvent(AuditEvent):
    """Every allowed field type."""

    event_type: ClassVar[str] = "testaudit.dummy"

    object_id: uuid.UUID
    colour: Colour
    level: Level
    count: int
    flag: bool
    at: datetime
    took: timedelta
    model_version: VersionStr
    name_hash: KeyedHash
    maybe_id: uuid.UUID | None = None


class TinyEvent(AuditEvent):
    event_type: ClassVar[str] = "testaudit.tiny"

    n: int
