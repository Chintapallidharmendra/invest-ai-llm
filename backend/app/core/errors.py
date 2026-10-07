"""RFC 9457 problem responses (ADR-003).

Every error is ``application/problem+json`` with ``type``, ``title``, ``status``,
``detail``, ``instance``, ``code`` and ``correlation_id``. Raise :class:`ProblemException`
for expected errors; never return ad-hoc error shapes.
"""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.obs import CORRELATION_HEADER, current_correlation_id, get_logger

PROBLEM_MEDIA_TYPE: Final = "application/problem+json"
PROBLEM_TYPE_PREFIX: Final = "urn:invest-ai-llm:problem:"

_HTTP_STATUS_CODES: Final[Mapping[int, str]] = {
    HTTPStatus.BAD_REQUEST: "bad_request",
    HTTPStatus.UNAUTHORIZED: "auth_required",
    HTTPStatus.FORBIDDEN: "forbidden",
    HTTPStatus.NOT_FOUND: "not_found",
    HTTPStatus.METHOD_NOT_ALLOWED: "method_not_allowed",
    HTTPStatus.CONFLICT: "conflict",
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE: "payload_too_large",
    HTTPStatus.UNSUPPORTED_MEDIA_TYPE: "unsupported_media_type",
    HTTPStatus.TOO_MANY_REQUESTS: "rate_limited",
}

_log = get_logger("core.errors")


class Problem(BaseModel):
    """OpenAPI schema of a problem body. Extension members are allowed."""

    model_config = ConfigDict(extra="allow")

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    code: str
    correlation_id: str | None = None


class ProblemException(Exception):  # noqa: N818 (name fixed by ADR-003)
    """An expected error rendered as a problem response.

    ``detail`` and extension members go to the client only; they are never logged.
    """

    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str | None = None,
        **ext: Any,
    ) -> None:
        # Keep the message content-free: logs may show str(exc).
        super().__init__(code)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.ext = ext


def problem_response(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    ext: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    correlation_id = current_correlation_id()
    body: dict[str, Any] = {
        "type": PROBLEM_TYPE_PREFIX + code,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "code": code,
        "correlation_id": correlation_id,
    }
    for key, value in (ext or {}).items():
        body.setdefault(key, value)
    response_headers = dict(headers or {})
    if correlation_id is not None:
        response_headers[CORRELATION_HEADER] = correlation_id
    return JSONResponse(
        body, status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=response_headers
    )


async def problem_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ProblemException)  # noqa: S101 (handler registered for this type)
    _log.info("http.problem", status_code=exc.status, error_code=exc.code)
    return problem_response(
        request,
        status=exc.status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        ext=exc.ext,
    )


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    # Field paths and error types only: messages and inputs can echo user content.
    errors = [{"loc": list(err.get("loc", ())), "type": err.get("type")} for err in exc.errors()]
    _log.info(
        "http.problem",
        status_code=HTTPStatus.UNPROCESSABLE_CONTENT,
        error_code="validation_error",
        error_count=len(errors),
    )
    return problem_response(
        request,
        status=HTTPStatus.UNPROCESSABLE_CONTENT,
        code="validation_error",
        title="Request validation failed",
        ext={"errors": errors},
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    code = _HTTP_STATUS_CODES.get(exc.status_code, "http_error")
    return problem_response(
        request,
        status=exc.status_code,
        code=code,
        title=HTTPStatus(exc.status_code).phrase,
        headers=exc.headers,
    )


def unhandled_exception_response(request: Request, exc: BaseException) -> JSONResponse:
    """500 problem with no exception text; the log gets type and location only."""
    _log.error(
        "http.unhandled_exception",
        exc_info=exc,
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        error_code="internal_error",
    )
    return problem_response(
        request,
        status=HTTPStatus.INTERNAL_SERVER_ERROR,
        code="internal_error",
        title="Internal server error",
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return unhandled_exception_response(request, exc)


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ProblemException, problem_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
