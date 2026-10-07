"""Content-free observability (ADR-033).

- Logs are structlog JSON. Only allow-listed fields are output; anything else is dropped.
- Exceptions are logged as type + code location only, never the message: parser and DB
  errors can embed document text.
- Request and response bodies are never logged.
- Registered scrubbers run on every event as a final safety net.
- Span attributes are allow-listed the same way, and spans never record exceptions.
"""

import logging
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Final, TextIO, cast

import structlog
from opentelemetry import trace
from structlog.typing import EventDict, FilteringBoundLogger, Processor, WrappedLogger

from app.core.config import LogLevel

CORRELATION_HEADER: Final = "X-Correlation-ID"

ALLOWED_LOG_FIELDS: Final = frozenset(
    {
        "timestamp",
        "level",
        "correlation_id",
        "user_id",
        "space_id",
        "module",
        "event",
        "latency_ms",
        "status_code",
        "error_code",
        "exc_type",
        "exc_location",
    }
)
COUNT_SUFFIX: Final = "_count"

Scrubber = Callable[[EventDict], EventDict]

_scrubbers: list[Scrubber] = []

_span_attributes: set[str] = {
    "correlation_id",
    "user_id",
    "space_id",
    "module",
    "latency_ms",
    "status_code",
    "error_code",
    "exc_type",
}


def _is_allowed(key: str, allowed: frozenset[str] | set[str]) -> bool:
    return key in allowed or key.endswith(COUNT_SUFFIX)


# --- Logging -----------------------------------------------------------------


def register_scrubber(fn: Scrubber) -> Scrubber:
    """Add a scrubber that runs on every log event before output. Usable as a decorator."""
    _scrubbers.append(fn)
    return fn


def exc_summary(exc: BaseException) -> dict[str, str]:
    """Describe an exception by type and innermost code location, without its message."""
    exc_cls = type(exc)
    if exc_cls.__module__ == "builtins":
        exc_type = exc_cls.__qualname__
    else:
        exc_type = f"{exc_cls.__module__}.{exc_cls.__qualname__}"

    tb = exc.__traceback__
    if tb is None:
        return {"exc_type": exc_type, "exc_location": "unknown"}
    while tb.tb_next is not None:
        tb = tb.tb_next
    frame = tb.tb_frame
    module = frame.f_globals.get("__name__", "?")
    return {
        "exc_type": exc_type,
        "exc_location": f"{module}:{frame.f_code.co_name}:{tb.tb_lineno}",
    }


def _resolve_exception(exc_info: object) -> BaseException | None:
    if isinstance(exc_info, BaseException):
        return exc_info
    if isinstance(exc_info, tuple) and len(exc_info) == 3:  # noqa: PLR2004 (exc_info triple)
        value = exc_info[1]
        return value if isinstance(value, BaseException) else None
    if exc_info:
        return sys.exc_info()[1]
    return None


def _summarise_exception(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    exc = _resolve_exception(event_dict.pop("exc_info", None))
    for key in ("exception", "stack", "stack_info"):
        event_dict.pop(key, None)
    if exc is not None:
        event_dict.update(exc_summary(exc))
    return event_dict


def _apply_allow_list(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    return {k: v for k, v in event_dict.items() if _is_allowed(k, ALLOWED_LOG_FIELDS)}


def _run_scrubbers(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    for scrub in _scrubbers:
        event_dict = scrub(event_dict)
    return event_dict


def _from_stdlib(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    # Library log messages can embed content (paths, queries, SQL parameters), so only
    # the logger name is kept.
    record = cast(logging.LogRecord, event_dict["_record"])
    event_dict["event"] = "stdlib_log"
    event_dict["module"] = record.name
    return event_dict


def _shared_processors() -> list[Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        _summarise_exception,
        _apply_allow_list,
        _run_scrubbers,
    ]


def _configure_structlog(level_no: int, out: TextIO) -> None:
    structlog.configure(
        processors=[*_shared_processors(), structlog.processors.JSONRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(level_no),
        logger_factory=structlog.WriteLoggerFactory(file=out),
        # Loggers resolve the current config on every call, so module-level loggers
        # created before configure_logging() still get the filters.
        cache_logger_on_first_use=False,
    )


def configure_logging(level: LogLevel = "INFO", stream: TextIO | None = None) -> None:
    """Configure structlog and route stdlib logging through the same filters."""
    out = stream if stream is not None else sys.stdout
    level_no = logging.getLevelNamesMapping()[level]
    shared = _shared_processors()
    _configure_structlog(level_no, out)

    handler = logging.StreamHandler(out)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[_from_stdlib, *shared],
            processors=[structlog.processors.JSONRenderer()],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level_no)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lib_logger = logging.getLogger(name)
        lib_logger.handlers = []
        lib_logger.propagate = True
    # The correlation middleware logs each request itself, without paths or queries.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(module: str) -> FilteringBoundLogger:
    # A lazy proxy: binding eagerly would freeze the config active at import time.
    return cast(FilteringBoundLogger, structlog.get_logger(module=module))


def current_correlation_id() -> str | None:
    value = structlog.contextvars.get_contextvars().get("correlation_id")
    return value if isinstance(value, str) else None


# --- Tracing -----------------------------------------------------------------


def allow_span_attributes(*names: str) -> None:
    """Allow-list extra span attributes. Never allow prompts, outputs or document text."""
    _span_attributes.update(names)


def filter_span_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in attributes.items() if _is_allowed(k, _span_attributes)}


def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    """Start a span with allow-listed attributes. Exceptions set the status only."""
    tracer = get_tracer("app")
    with tracer.start_as_current_span(
        name,
        attributes=filter_span_attributes(attributes),
        record_exception=False,
        set_status_on_exception=True,
    ) as current:
        yield current


# Content-free defaults from import time on: structlog's own default renders full
# tracebacks and every field.
_configure_structlog(logging.INFO, sys.stdout)
