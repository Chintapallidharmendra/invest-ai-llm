"""Time helpers. All times are timezone-aware UTC."""

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)
