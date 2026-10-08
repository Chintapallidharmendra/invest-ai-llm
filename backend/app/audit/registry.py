"""Registry of audit event models (ADR-032).

Each module declares its events in ``app/<module>/audit_events.py`` as subclasses of
:class:`app.audit.types.AuditEvent`; defining the class registers it. :func:`discover`
imports every such module (the CI check and the writer's callers rely on it).
"""

import importlib
import importlib.util
import pkgutil
import re
from typing import TYPE_CHECKING, Final

import app

if TYPE_CHECKING:
    from app.audit.types import AuditEvent

EVENT_TYPE_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

_models: dict[str, type["AuditEvent"]] = {}


def register(model: type["AuditEvent"]) -> None:
    event_type = model.event_type
    if not EVENT_TYPE_PATTERN.fullmatch(event_type):
        raise ValueError(f"event_type must be '<domain>.<action>': {event_type!r}")
    existing = _models.get(event_type)
    if existing is not None and existing.__qualname__ != model.__qualname__:
        raise ValueError(f"audit event already registered: {event_type}")
    _models[event_type] = model


def unregister(event_type: str) -> None:
    _models.pop(event_type, None)


def get(event_type: str) -> type["AuditEvent"] | None:
    return _models.get(event_type)


def registered() -> dict[str, type["AuditEvent"]]:
    return dict(_models)


def discover() -> list[str]:
    """Import every ``app/<module>/audit_events.py``; returns the module names."""
    imported = []
    for info in sorted(pkgutil.iter_modules(app.__path__), key=lambda i: i.name):
        name = f"app.{info.name}.audit_events"
        if info.ispkg and importlib.util.find_spec(name) is not None:
            importlib.import_module(name)
            imported.append(name)
    return imported
