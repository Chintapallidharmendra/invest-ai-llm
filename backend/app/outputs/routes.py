"""Outputs API (FR-024): list, detail, delete, download links, and the download itself.

``GET /downloads/{token}`` needs a session of the user the link was issued to; for
anyone else, or after expiry or loss of access, it's a 404. Responses are never cached.
"""

import uuid
from http import HTTPStatus
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse

from app.auth.deps import CurrentUser, require_role
from app.auth.models import Role
from app.core.errors import Problem, ProblemException
from app.outputs import service
from app.outputs.schemas import DownloadLink, OutputDetailOut, OutputList, OutputOut
from app.spaces.deps import build_access_context

router = APIRouter(tags=["outputs"])

_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": Problem}
    for status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND)
}
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}

User = Annotated[CurrentUser, Depends(require_role(Role.USER))]


def _not_found() -> ProblemException:
    return ProblemException(HTTPStatus.NOT_FOUND, "output_not_found", "Output not found")


def _out(view: service.OutputView) -> OutputOut:
    return OutputOut(
        id=view.id,
        space_id=view.space_id,
        kind=view.kind,
        origin=view.origin,
        title=view.title,
        size_bytes=view.size_bytes,
        source_document_ids=list(view.source_document_ids),
        version_of_id=view.version_of_id,
        created_at=view.created_at,
        expires_at=view.expires_at,
    )


@router.get("/outputs", responses=_RESPONSES)
async def list_outputs(user: User, space_id: uuid.UUID | None = None) -> OutputList:
    ctx = await build_access_context(user)
    return OutputList(items=[_out(v) for v in await service.list_outputs(ctx, space_id=space_id)])


@router.get("/outputs/{output_id}", responses=_RESPONSES)
async def get_output(output_id: uuid.UUID, user: User) -> OutputDetailOut:
    ctx = await build_access_context(user)
    try:
        detail = await service.get_output(ctx, output_id)
    except service.OutputNotFoundError:
        raise _not_found() from None
    return OutputDetailOut(**_out(detail).model_dump(), versions=[_out(v) for v in detail.versions])


@router.delete("/outputs/{output_id}", status_code=HTTPStatus.NO_CONTENT, responses=_RESPONSES)
async def delete_output(output_id: uuid.UUID, user: User) -> Response:
    ctx = await build_access_context(user)
    try:
        await service.delete_output(ctx, output_id)
    except service.OutputNotFoundError:
        raise _not_found() from None
    return Response(status_code=HTTPStatus.NO_CONTENT)


@router.post(
    "/outputs/{output_id}/download-links", status_code=HTTPStatus.CREATED, responses=_RESPONSES
)
async def issue_download_link(output_id: uuid.UUID, user: User, response: Response) -> DownloadLink:
    ctx = await build_access_context(user)
    try:
        issued = await service.issue_download_link(ctx, output_id)
    except service.OutputNotFoundError:
        raise _not_found() from None
    response.headers.update(_NO_STORE)
    return DownloadLink(url=issued.url, expires_at=issued.expires_at)


def _disposition(filename: str) -> str:
    ascii_name = filename.encode("ascii", "replace").decode().replace("?", "_").replace('"', "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


@router.get(
    "/downloads/{token}",
    response_class=StreamingResponse,
    responses={**_RESPONSES, HTTPStatus.OK: {"content": {"application/octet-stream": {}}}},
)
async def download(token: str, user: User) -> StreamingResponse:
    ctx = await build_access_context(user)
    try:
        opened = await service.open_download(ctx, token, username=user.username)
    except service.OutputNotFoundError:
        raise _not_found() from None
    except service.OutputTooLargeError:
        raise ProblemException(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "output_too_large", "Output too large"
        ) from None
    return StreamingResponse(
        opened.chunks,
        media_type=opened.media_type,
        headers={
            **_NO_STORE,
            "Content-Disposition": _disposition(opened.filename),
            "X-Content-Type-Options": "nosniff",
        },
    )
