"""CSRF protection and the session/CSRF cookies (ADR-006).

**Cookies.** ``__Host-session`` (HttpOnly) carries the session token; ``__Host-csrf``
is readable by JS so the frontend can echo it. The ``__Host-`` prefix makes browsers
insist on ``Secure``, ``Path=/`` and no ``Domain``, so another subdomain can't set or
overwrite them. Headers are written out in full so the attributes are exactly these.

**Middleware.** Every ``POST``/``PUT``/``PATCH``/``DELETE`` under ``/api/v1`` must:

1. pass the origin check: ``Origin`` is one of ``AuthSettings.site_origins``, or the
   browser says ``Sec-Fetch-Site: same-origin``;
2. send ``X-CSRF-Token`` equal to the ``__Host-csrf`` cookie (double submit, compared
   in constant time), except on the pre-session endpoints in
   :data:`TOKEN_EXEMPT_PATHS`.

Failures get ``403 csrf_failed``. Pure ASGI: the body is never read.
"""

import hmac
from typing import Final

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth.settings import get_auth_settings
from app.core.errors import problem_response
from app.core.obs import get_logger

SESSION_COOKIE: Final = "__Host-session"
CSRF_COOKIE: Final = "__Host-csrf"
CSRF_HEADER: Final = "X-CSRF-Token"

API_PREFIX: Final = "/api/v1/"
STATE_CHANGING: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# No session (and so no CSRF cookie) exists yet on these; the origin check still applies.
TOKEN_EXEMPT_PATHS: Final = frozenset({"/api/v1/auth/login", "/api/v1/auth/set-password"})

_SESSION_ATTRS: Final = "HttpOnly; Secure; SameSite=Strict; Path=/"
_CSRF_ATTRS: Final = "Secure; SameSite=Strict; Path=/"

_log = get_logger("auth.csrf")


# --- Cookies ---------------------------------------------------------------------------


def set_login_cookies(response: Response, *, session_token: str, csrf_token: str) -> None:
    response.headers.append("set-cookie", f"{SESSION_COOKIE}={session_token}; {_SESSION_ATTRS}")
    response.headers.append("set-cookie", f"{CSRF_COOKIE}={csrf_token}; {_CSRF_ATTRS}")


def expire_login_cookies(response: Response) -> None:
    response.headers.append("set-cookie", f"{SESSION_COOKIE}=; Max-Age=0; {_SESSION_ATTRS}")
    response.headers.append("set-cookie", f"{CSRF_COOKIE}=; Max-Age=0; {_CSRF_ATTRS}")


# --- Checks ----------------------------------------------------------------------------


def origin_allowed(headers: Headers, site_origins: list[str]) -> bool:
    origin = headers.get("origin")
    if (
        origin is not None
        and origin != "null"
        and origin.rstrip("/") in {o.rstrip("/") for o in site_origins}
    ):
        return True
    return headers.get("sec-fetch-site") == "same-origin"


def token_matches(header: str | None, cookie: str | None) -> bool:
    if not header or not cookie:
        return False
    return hmac.compare_digest(header.encode(), cookie.encode())


def needs_check(scope: Scope) -> bool:
    return (
        scope["type"] == "http"
        and scope.get("method", "") in STATE_CHANGING
        and str(scope.get("path", "")).startswith(API_PREFIX)
    )


class CsrfMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not needs_check(scope):
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        failure = self._failure(request)
        if failure is None:
            await self.app(scope, receive, send)
            return
        _log.info("auth.csrf_failed", error_code=f"csrf_{failure}")
        response = problem_response(
            request, status=403, code="csrf_failed", title="CSRF check failed"
        )
        await response(scope, receive, send)

    @staticmethod
    def _failure(request: Request) -> str | None:
        if not origin_allowed(request.headers, get_auth_settings().site_origins):
            return "origin"
        if request.url.path in TOKEN_EXEMPT_PATHS:
            return None
        if not token_matches(request.headers.get(CSRF_HEADER), request.cookies.get(CSRF_COOKIE)):
            return "token"
        return None
