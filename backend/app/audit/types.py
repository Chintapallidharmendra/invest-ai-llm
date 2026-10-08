"""Audit event models: metadata only, enforced by type (ADR-032).

An event is a frozen Pydantic model with a class-level ``event_type``::

    class DocumentDeleted(AuditEvent):
        event_type: ClassVar[str] = "documents.deleted"
        document_id: uuid.UUID
        reason: DeletionReason            # an enum
        size_bytes: int

Allowed field types: ``uuid.UUID``, enums, ``int``, ``bool``, ``datetime`` (aware),
``timedelta``, :class:`VersionStr` and :class:`KeyedHash`, each optionally ``| None``.
``python -m app.audit.check_models`` rejects anything else, and the writer refuses
such models at run time too.
"""

import hashlib
import hmac
import re
from enum import StrEnum
from functools import lru_cache
from typing import Any, ClassVar, Final, Self

from pydantic import BaseModel, ConfigDict, GetCoreSchemaHandler
from pydantic_core import core_schema

from app.audit import registry
from app.audit.settings import get_audit_settings

MIN_KEY_BYTES: Final = 32


class AuditPriority(StrEnum):
    NORMAL = "normal"
    HIGH = "high"  # break-glass events (ADR-036)


class _PatternStr(str):
    """A ``str`` subclass validated against ``pattern`` (full match)."""

    __slots__ = ()
    pattern: ClassVar[re.Pattern[str]]

    @classmethod
    def validate(cls, value: str) -> Self:
        if not isinstance(value, str) or not cls.pattern.fullmatch(value):
            raise ValueError(f"not a valid {cls.__name__}")
        return cls(value)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls.validate, core_schema.str_schema(max_length=64)
        )


class VersionStr(_PatternStr):
    """A model or prompt version: ``3``, ``v1.2``, ``2.0.1-rc1``, ``1.4+build.7``.

    Digits lead, and the only words allowed are the pre-release labels ``alpha``,
    ``beta``, ``rc``, ``dev``, ``post`` and ``build``: nothing else fits.
    """

    __slots__ = ()
    pattern = re.compile(
        r"v?[0-9]{1,6}(?:\.[0-9]{1,6}){0,3}(?:[-+](?:alpha|beta|rc|dev|post|build)?\.?[0-9]{1,6})?"
    )


class KeyedHash(_PatternStr):
    """HMAC-SHA256 of a value under the audit key, as 64 lowercase hex digits.

    Lets compliance match equal values across events without storing them; unlike a
    plain hash it can't be confirmed by hashing a guess (ADR-032).
    """

    __slots__ = ()
    pattern = re.compile(r"[0-9a-f]{64}")

    @classmethod
    def of(cls, value: str | bytes) -> "KeyedHash":
        data = value.encode() if isinstance(value, str) else value
        return cls(hmac.new(audit_key(), data, hashlib.sha256).hexdigest())


class AuditKeyError(RuntimeError):
    """The audit HMAC key file is missing, unreadable or too short."""


@lru_cache(maxsize=1)
def audit_key() -> bytes:
    path = get_audit_settings().hmac_key_path
    try:
        key = path.read_bytes().strip()
    except OSError as exc:
        raise AuditKeyError(f"audit HMAC key unreadable ({type(exc).__name__}): {path}") from None
    if len(key) < MIN_KEY_BYTES:
        raise AuditKeyError(f"audit HMAC key must be at least {MIN_KEY_BYTES} bytes: {path}")
    return key


class AuditEvent(BaseModel):
    """Base of every audit event model. Subclasses with an ``event_type`` register."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=False)

    event_type: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "event_type" in cls.__dict__:
            registry.register(cls)
