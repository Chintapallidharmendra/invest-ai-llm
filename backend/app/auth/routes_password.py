"""``POST /auth/change-password``: change your own password (Story 2.4).

Authenticated and CSRF-protected (it isn't in ``csrf.TOKEN_EXEMPT_PATHS``).
"""

from http import HTTPStatus

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict

from app.auth import password_change
from app.auth.deps import CurrentUser, current_user
from app.auth.password_change import ChangeFailure
from app.core.errors import Problem, ProblemException

router = APIRouter(prefix="/auth", tags=["auth"])


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # No length limits: oversized values fail like any other wrong or rejected password.
    current_password: str
    new_password: str


_TITLES = {
    ChangeFailure.CURRENT_PASSWORD_INVALID: "Current password is incorrect",
    ChangeFailure.PASSWORD_REJECTED: "Password does not meet the policy",
    ChangeFailure.PASSWORD_UNCHANGED: "New password must differ from the current one",
}


@router.post(
    "/change-password",
    status_code=HTTPStatus.NO_CONTENT,
    responses={
        HTTPStatus.BAD_REQUEST: {"model": Problem},
        HTTPStatus.UNAUTHORIZED: {"model": Problem},
        HTTPStatus.UNPROCESSABLE_CONTENT: {"model": Problem},
    },
)
async def change_password(
    body: ChangePasswordRequest,
    user: CurrentUser = Depends(current_user),  # noqa: B008
) -> Response:
    outcome = await password_change.change_password(user, body.current_password, body.new_password)
    if isinstance(outcome, password_change.Rejected):
        failure = outcome.failure
        status = (
            HTTPStatus.BAD_REQUEST
            if failure is ChangeFailure.CURRENT_PASSWORD_INVALID
            else HTTPStatus.UNPROCESSABLE_CONTENT
        )
        if outcome.reasons:
            raise ProblemException(
                status,
                failure.value,
                _TITLES[failure],
                reasons=[r.value for r in outcome.reasons],
            )
        raise ProblemException(status, failure.value, _TITLES[failure])
    return Response(status_code=HTTPStatus.NO_CONTENT)
