"""Job type registry (ADR-017).

Feature modules register their job types at import time, in ``app/<module>/jobs.py``
(the worker imports every such module)::

    class IngestPayload(BaseModel):
        document_id: UUID

    async def ingest(ctx: JobContext, payload: IngestPayload) -> None: ...

    register("ingest", IngestPayload, ingest)

**Payloads hold IDs and enums only, never content.** A payload model may use UUIDs,
enums, ``Literal`` values, ints, bools, dates/datetimes, :data:`Identifier` strings,
nested payload models, and optional/list/tuple/set forms of those. Any other field
(a free-text ``str``, ``float``, ``dict``, ``Any``, ``bytes``…) raises
:class:`PayloadModelError` when the type is registered, i.e. at import time.
"""

import re
import types
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Annotated, Any, Final, Literal, Union, get_args, get_origin

from pydantic import BaseModel, StringConstraints, ValidationError

IDENTIFIER_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$"
TYPE_NAME_PATTERN: Final = r"^[a-z][a-z0-9_]{0,63}$"


class _IdentifierMarker:
    """Annotated marker: this ``str`` is a constrained identifier, not free text."""

    def __repr__(self) -> str:
        return "Identifier"


IDENTIFIER_MARKER: Final = _IdentifierMarker()

# A short machine identifier (template name, sheet key, …): 1-64 chars, no spaces.
Identifier = Annotated[
    str, StringConstraints(pattern=IDENTIFIER_PATTERN, max_length=64), IDENTIFIER_MARKER
]

_SCALARS: Final[tuple[type, ...]] = (uuid.UUID, int, bool, date)  # date covers datetime
_CONTAINERS: Final = (list, tuple, set, frozenset)


class PayloadModelError(TypeError):
    """A payload model declares a field that could carry content."""


class UnknownJobTypeError(LookupError):
    pass


class InvalidPayloadError(ValueError):
    """The payload does not match the job type's model. Carries no field values."""

    def __init__(self, job_type: str, error_count: int) -> None:
        super().__init__(f"invalid payload for job type {job_type!r} ({error_count} errors)")
        self.job_type = job_type
        self.error_count = error_count


class JobError(Exception):
    """Raise from a handler to fail with a specific ``error_code`` (retried as usual).

    The message is never stored or logged; only ``code`` is.
    """

    def __init__(self, code: str, *, retry: bool = True) -> None:
        super().__init__(code)
        self.code = code
        self.retry = retry


class PermanentJobError(JobError):
    """A failure that retrying cannot fix: the job goes straight to ``failed``."""

    def __init__(self, code: str) -> None:
        super().__init__(code, retry=False)


@dataclass(frozen=True)
class JobContext:
    """What a handler gets besides its payload."""

    job_id: uuid.UUID
    job_type: str
    user_id: uuid.UUID | None
    space_id: uuid.UUID | None
    attempt: int
    max_attempts: int
    correlation_id: str | None
    _progress: Callable[[uuid.UUID, int, int], Awaitable[None]] = field(repr=False)

    async def progress(self, done: int, total: int) -> None:
        """Record ``done`` of ``total`` steps in ``jobs.progress``."""
        await self._progress(self.job_id, done, total)


type Handler[P: BaseModel] = Callable[[JobContext, P], Awaitable[None]]


@dataclass(frozen=True)
class JobType[P: BaseModel]:
    name: str
    payload_model: type[P]
    handler: Handler[P]
    llm_heavy: bool


_types: dict[str, JobType[Any]] = {}


def _field_error(model: type[BaseModel], name: str, annotation: object) -> PayloadModelError:
    return PayloadModelError(
        f"{model.__qualname__}.{name}: {annotation!r} is not allowed in a job payload; "
        "use UUIDs, enums, ints, bools, dates or Identifier (no free text)"
    )


def _allowed(  # noqa: PLR0911 (one return per kind of type)
    tp: object, metadata: tuple[object, ...], seen: set[type[BaseModel]]
) -> bool:
    origin = get_origin(tp)
    if origin is Annotated:
        inner, *extra = get_args(tp)
        return _allowed(inner, (*metadata, *extra), seen)
    if tp is type(None):
        return True
    if origin is Union or origin is types.UnionType:
        return all(_allowed(arg, metadata, seen) for arg in get_args(tp))
    if origin is Literal:
        return all(isinstance(v, int | str | Enum) for v in get_args(tp))
    if origin in _CONTAINERS:
        args = [a for a in get_args(tp) if a is not Ellipsis]
        return bool(args) and all(_allowed(a, (), seen) for a in args)
    if not isinstance(tp, type):
        return False
    if issubclass(tp, Enum):
        return True
    if issubclass(tp, BaseModel):
        check_payload_model(tp, _seen=seen)
        return True
    if issubclass(tp, str):
        return IDENTIFIER_MARKER in metadata
    return issubclass(tp, _SCALARS)


def check_payload_model(
    model: type[BaseModel], *, _seen: set[type[BaseModel]] | None = None
) -> None:
    """Raise :class:`PayloadModelError` unless every field is an ID, enum or scalar."""
    seen = set() if _seen is None else _seen
    if model in seen:
        return
    seen.add(model)
    for name, info in model.model_fields.items():
        if not _allowed(info.annotation, tuple(info.metadata), seen):
            raise _field_error(model, name, info.annotation)


def register[P: BaseModel](
    type: str,
    payload_model: type[P],
    handler: Handler[P],
    llm_heavy: bool = False,
) -> JobType[P]:
    """Register a job type. Call at import time; raises on a content-capable payload."""
    if not re.fullmatch(TYPE_NAME_PATTERN, type):
        raise ValueError(f"job type name must match {TYPE_NAME_PATTERN}: {type!r}")
    if type in _types:
        raise ValueError(f"job type already registered: {type}")
    check_payload_model(payload_model)
    job_type = JobType(type, payload_model, handler, llm_heavy)
    _types[type] = job_type
    return job_type


def unregister(type: str) -> None:
    _types.pop(type, None)


def get(type: str) -> JobType[Any]:
    try:
        return _types[type]
    except KeyError:
        raise UnknownJobTypeError(type) from None


def registered() -> Mapping[str, JobType[Any]]:
    return dict(_types)


def validate_payload(
    job_type: JobType[Any], payload: BaseModel | Mapping[str, Any]
) -> dict[str, Any]:
    """Validate against the type's model; return the JSON-ready dict that is stored."""
    raw = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else dict(payload)
    try:
        model = job_type.payload_model.model_validate(raw)
    except ValidationError as exc:
        # The pydantic message can echo input values; keep only the count.
        raise InvalidPayloadError(job_type.name, exc.error_count()) from None
    data: dict[str, Any] = model.model_dump(mode="json")
    return data
