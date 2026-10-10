"""``POST /auth/set-password``: redeem a one-time set-password link (Story 2.3).

No session is needed (the link is how a user gets one), so the route is exempt from the
CSRF token check; the origin check still applies.
"""

from http import HTTPStatus

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict

from app.auth import links
from app.core.errors import Problem, ProblemException

router = APIRouter(prefix="/auth", tags=["auth"])


class SetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # No length limits: an oversized value fails as an invalid link or a rejected
    # password, like any other.
    token: str
    username: str
    new_password: str


@router.post(
    "/set-password",
    status_code=HTTPStatus.NO_CONTENT,
    responses={
        HTTPStatus.BAD_REQUEST: {"model": Problem},
        HTTPStatus.UNPROCESSABLE_CONTENT: {"model": Problem},
    },
)
async def set_password(body: SetPasswordRequest) -> Response:
    try:
        await links.redeem(body.token, body.username, body.new_password)
    except links.PasswordRejectedError as exc:
        raise ProblemException(
            HTTPStatus.UNPROCESSABLE_CONTENT,
            "password_rejected",
            "Password does not meet the policy",
            reasons=[r.value for r in exc.reasons],
        ) from None
    except links.LinkInvalidError:
        raise ProblemException(
            HTTPStatus.BAD_REQUEST, "link_invalid", "This link is invalid or expired"
        ) from None
    return Response(status_code=HTTPStatus.NO_CONTENT)
