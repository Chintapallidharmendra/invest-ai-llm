"""CI check: every audit event model is metadata-only by type (ADR-032).

    python -m app.audit.check_models

Imports every ``app/<module>/audit_events.py`` and exits non-zero if any model has a
field outside the allowed types (UUID, enum, int, bool, datetime, timedelta,
VersionStr, KeyedHash, each optionally ``| None``): plain or constrained ``str``,
``bytes``, ``float``, containers, ``Any`` and nested models are all rejected.
"""

import sys
import types
import typing
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from enum import Enum
from functools import cache
from typing import Any, Final

from app.audit import registry
from app.audit.types import AuditEvent, KeyedHash, VersionStr

_ALLOWED: Final = (uuid.UUID, int, bool, datetime, timedelta, VersionStr, KeyedHash)


def _type_problem(annotation: Any) -> str | None:  # noqa: PLR0911
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        members = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(members) != 1:
            return "unions other than 'X | None' are not allowed"
        return _type_problem(members[0])
    if origin is not None:
        return f"{origin!r} is not an allowed audit field type"
    if not isinstance(annotation, type):
        return f"{annotation!r} is not an allowed audit field type"
    if issubclass(annotation, Enum):
        if issubclass(annotation, str | int):
            return None
        return "enums must be StrEnum or IntEnum"
    if annotation in _ALLOWED:
        return None
    if issubclass(annotation, str):
        return "free-text str is not allowed (use an enum, VersionStr or KeyedHash)"
    return f"{annotation.__name__} is not an allowed audit field type"


def violations(model: type[AuditEvent]) -> list[str]:
    """Problems with ``model``'s fields, as ``"<field>: <reason>"``; empty when valid."""
    problems = []
    for name, field in model.model_fields.items():
        problem = _type_problem(field.annotation)
        if problem is None and field.metadata:
            problem = "constrained or annotated types are not allowed"
        if problem is not None:
            problems.append(f"{name}: {problem}")
    return problems


@cache
def is_valid(model: type[AuditEvent]) -> bool:
    return not violations(model)


def main(argv: Sequence[str] | None = None) -> int:
    registry.discover()
    models = registry.registered()
    failures = 0
    for event_type, model in sorted(models.items()):
        for problem in violations(model):
            failures += 1
            print(f"{model.__module__}.{model.__qualname__} ({event_type}) {problem}")
    if failures:
        print(f"check_models: {failures} problem(s) in {len(models)} audit event model(s)")
        return 1
    print(f"check_models: {len(models)} audit event model(s) OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
