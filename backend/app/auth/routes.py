"""Login, logout and the current user (ADR-006).

This router also mounts every ``app/auth/routes_*.py`` that exposes
``router: APIRouter``, so later stories add endpoints in files of their own (give
those routers ``prefix="/auth"`` where the paths belong under it).
"""

import importlib
import pkgutil
from http import HTTPStatus

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

import app.auth
from app.auth import service
from app.auth.csrf import expire_login_cookies, set_login_cookies
from app.auth.deps import CurrentUser, current_user, session_token
from app.auth.ratelimit import RateLimitUnavailableError, hit
from app.auth.schemas import LoginRequest, Me
from app.auth.settings import get_auth_settings
from app.core.errors import Problem, ProblemException, problem_response

_PROBLEM = {"model": Problem}

auth_router = APIRouter(prefix="/auth", tags=["auth"])


def _me(user: CurrentUser) -> Me:
    return Me(id=user.id, username=user.username, role=user.role)


async def _retry_after(request: Request) -> int | None:
    """Seconds to wait if this client IP is over the login limit, else ``None``."""
    settings = get_auth_settings()
    ip = request.client.host if request.client else "unknown"
    try:
        result = await hit(
            "login", ip, limit=settings.login_rate_limit, window_s=settings.login_rate_window_s
        )
    except RateLimitUnavailableError:
        # Fail closed (ADR-018).
        raise ProblemException(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "rate_limit_unavailable",
            "Sign-in is temporarily unavailable",
        ) from None
    return None if result.allowed else result.retry_after_s


@auth_router.post(
    "/login",
    response_model=Me,
    responses={
        HTTPStatus.UNAUTHORIZED: _PROBLEM,
        HTTPStatus.TOO_MANY_REQUESTS: _PROBLEM,
        HTTPStatus.SERVICE_UNAVAILABLE: _PROBLEM,
    },
)
async def login(body: LoginRequest, request: Request, response: Response) -> Me | JSONResponse:
    retry_after = await _retry_after(request)
    if retry_after is not None:
        return problem_response(
            request,
            status=HTTPStatus.TOO_MANY_REQUESTS,
            code="rate_limited",
            title="Too many sign-in attempts",
            ext={"retry_after_s": retry_after},
            headers={"Retry-After": str(retry_after)},
        )
    outcome = await service.login(
        body.username,
        body.password,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        previous_token=session_token(request),
    )
    if isinstance(outcome, service.LoginFailure):
        raise ProblemException(
            HTTPStatus.UNAUTHORIZED, "invalid_credentials", "Invalid username or password"
        )
    set_login_cookies(response, session_token=outcome.session_token, csrf_token=outcome.csrf_token)
    return _me(outcome.user)


@auth_router.post(
    "/logout",
    status_code=HTTPStatus.NO_CONTENT,
    responses={HTTPStatus.UNAUTHORIZED: _PROBLEM},
)
async def logout(user: CurrentUser = Depends(current_user)) -> Response:  # noqa: B008
    await service.logout(user)
    response = Response(status_code=HTTPStatus.NO_CONTENT)
    expire_login_cookies(response)
    return response


@auth_router.get("/me", responses={HTTPStatus.UNAUTHORIZED: _PROBLEM})
async def me(user: CurrentUser = Depends(current_user)) -> Me:  # noqa: B008
    return _me(user)


def discover_subrouters() -> list[str]:
    """Names of the ``app.auth.routes_*`` modules that expose a router."""
    names = []
    for info in sorted(pkgutil.iter_modules(app.auth.__path__), key=lambda m: m.name):
        if info.name.startswith("routes_") and not info.ispkg:
            names.append(f"app.auth.{info.name}")
    return names


router = APIRouter()
router.include_router(auth_router)
for _name in discover_subrouters():
    _sub = getattr(importlib.import_module(_name), "router", None)
    if isinstance(_sub, APIRouter):
        router.include_router(_sub)
