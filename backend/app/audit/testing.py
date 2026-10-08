"""Canary helper for every module's tests (NFR-023)::

    from app.audit.testing import assert_no_content

    assert_no_content(row.payload)          # a stored payload
    assert_no_content(MyEvent(...))         # or an event model

Fails if any string value is not an allow-listed format: a UUID, a ``VersionStr``, a
``KeyedHash``, an audit timestamp, or a value of an enum used by a registered audit
event model. Floats, nested objects and lists are rejected too.
"""

import re
import typing
import uuid
from collections.abc import Iterable, Mapping
from enum import Enum
from typing import Any, Final

from app.audit import registry
from app.audit.chain import payload_of
from app.audit.types import AuditEvent, AuditPriority, KeyedHash, VersionStr

_TIMESTAMP: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z")


def _enums_of(annotation: Any) -> Iterable[type[Enum]]:
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        yield annotation
    for arg in typing.get_args(annotation):
        yield from _enums_of(arg)


def allowed_enum_values() -> frozenset[str]:
    """Values of every enum that a registered audit event model uses."""
    registry.discover()
    values: set[str] = {p.value for p in AuditPriority}
    for model in registry.registered().values():
        for field in model.model_fields.values():
            for enum in _enums_of(field.annotation):
                values.update(str(m.value) for m in enum)
    return frozenset(values)


def _allowed_string(value: str, enum_values: frozenset[str]) -> bool:
    if value in enum_values:
        return True
    if VersionStr.pattern.fullmatch(value) or KeyedHash.pattern.fullmatch(value):
        return True
    if _TIMESTAMP.fullmatch(value):
        return True
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def assert_no_content(
    payload: Mapping[str, Any] | AuditEvent, *, extra_enum_values: Iterable[str] = ()
) -> None:
    data = payload_of(payload) if isinstance(payload, AuditEvent) else payload
    enum_values = allowed_enum_values() | frozenset(extra_enum_values)
    for key, value in data.items():
        if value is None or isinstance(value, bool | int):
            continue
        if isinstance(value, str) and _allowed_string(value, enum_values):
            continue
        # The message names the key only: the value may be the very content we caught.
        raise AssertionError(
            f"audit payload field {key!r} ({type(value).__name__}) is not an allow-listed "
            "enum, UUID, version, keyed hash or timestamp"
        )
