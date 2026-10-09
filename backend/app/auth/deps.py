"""FastAPI dependencies for authentication and roles (ADR-006). **LOCKED:** every
authenticated endpoint uses these::

    @router.get("/things")
    async def things(user: CurrentUser = Depends(current_user)) -> ...

    @router.post("/admin/users")
    async def create(user: CurrentUser = Depends(require_role(Role.ADMIN))) -> ...

The session is looked up in Postgres on every request, so revocation takes effect at
once.
"""

from collections.abc import Awaitable, Callable
from http import HTTPStatus

from fastapi import Depends, Request

from app.audit import writer
from app.auth.audit_events import AccessDenied
from app.auth.csrf import SESSION_COOKIE
from app.auth.models import Role
from app.auth.service import SessionUser, authenticate, correlation_id
from app.core.errors import ProblemException

CurrentUser = SessionUser

__all__ = ["CurrentUser", "current_user", "require_role", "session_token"]


def session_token(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE) or None


async def current_user(request: Request) -> CurrentUser:
    user = await authenticate(session_token(request))
    if user is None:
        raise ProblemException(HTTPStatus.UNAUTHORIZED, "not_authenticated", "Not signed in")
    return user


def require_role(*roles: Role) -> Callable[..., Awaitable[CurrentUser]]:
    """A dependency allowing only ``roles``; others get 403 and an audit row."""
    allowed = frozenset(roles)
    if not allowed:
        raise ValueError("require_role needs at least one role")

    async def dependency(user: CurrentUser = Depends(current_user)) -> CurrentUser:  # noqa: B008
        if user.role not in allowed:
            await writer.record(
                AccessDenied(role=user.role),
                actor_user_id=user.id,
                correlation_id=correlation_id(),
            )
            raise ProblemException(HTTPStatus.FORBIDDEN, "forbidden", "Forbidden")
        return user

    return dependency
