"""Correlation-ID middleware (ADR-033).

Accepts a valid UUID in ``X-Correlation-ID``, otherwise generates a UUIDv7. The ID is
bound to the structlog context and echoed in the response header. Pure ASGI, so the
context is shared with the request's handlers and streaming responses.

It also turns any exception that escapes the app into a 500 problem, so the server
never logs exception messages.
"""

import time
import uuid
from http import HTTPStatus

import structlog
from prometheus_client import Counter, Histogram
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import unhandled_exception_response
from app.core.ids import new_uuid7
from app.core.obs import CORRELATION_HEADER, get_logger

_CANONICAL_UUID_LEN = 36

REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "status_code"])
LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency", ["method"])

_log = get_logger("api.middleware")


def accept_or_generate(value: str | None) -> str:
    """Return ``value`` normalised if it is a canonical UUID, else a new UUIDv7."""
    if value is not None and len(value) == _CANONICAL_UUID_LEN:
        try:
            return str(uuid.UUID(value))
        except ValueError:
            pass
    return str(new_uuid7())


class CorrelationIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        correlation_id = accept_or_generate(Headers(scope=scope).get(CORRELATION_HEADER))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        scope.setdefault("state", {})["correlation_id"] = correlation_id

        status_code = int(HTTPStatus.INTERNAL_SERVER_ERROR)
        started = False

        async def send_with_header(message: Message) -> None:
            nonlocal status_code, started
            if message["type"] == "http.response.start":
                started = True
                status_code = message["status"]
                MutableHeaders(scope=message)[CORRELATION_HEADER] = correlation_id
            await send(message)

        start = time.perf_counter()
        try:
            await self.app(scope, receive, send_with_header)
        except Exception as exc:  # last-resort handler, see module doc
            if started:
                _log.error("http.unhandled_after_start", exc_info=exc, error_code="internal_error")
            else:
                response = unhandled_exception_response(Request(scope), exc)
                await response(scope, receive, send_with_header)
        finally:
            elapsed = time.perf_counter() - start
            method = scope.get("method", "")
            REQUESTS.labels(method=method, status_code=str(status_code)).inc()
            LATENCY.labels(method=method).observe(elapsed)
            _log.info("http.request", status_code=status_code, latency_ms=round(elapsed * 1000, 1))
            structlog.contextvars.clear_contextvars()
