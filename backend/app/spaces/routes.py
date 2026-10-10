"""``GET /spaces``. Also mounts every ``app/spaces/routes_*.py`` router as is, so later
stories (8.3 workspaces) add endpoints in files of their own."""

import importlib
import pkgutil
from http import HTTPStatus

from fastapi import APIRouter, Depends

import app.spaces
from app.core.errors import Problem
from app.spaces import service
from app.spaces.access import AccessContext
from app.spaces.deps import access_context
from app.spaces.schemas import SpaceList, SpaceOut

spaces_router = APIRouter(prefix="/spaces", tags=["spaces"])


@spaces_router.get("", responses={HTTPStatus.UNAUTHORIZED: {"model": Problem}})
async def list_spaces(ctx: AccessContext = Depends(access_context)) -> SpaceList:  # noqa: B008
    spaces = await service.list_spaces(ctx)
    return SpaceList(
        items=[
            SpaceOut(id=s.id, kind=s.kind, my_role=s.my_role, code_name=s.code_name) for s in spaces
        ]
    )


def discover_subrouters() -> list[str]:
    """Names of the ``app.spaces.routes_*`` modules."""
    return [
        f"app.spaces.{info.name}"
        for info in sorted(pkgutil.iter_modules(app.spaces.__path__), key=lambda m: m.name)
        if info.name.startswith("routes_") and not info.ispkg
    ]


router = APIRouter()
router.include_router(spaces_router)
for _name in discover_subrouters():
    _sub = getattr(importlib.import_module(_name), "router", None)
    if isinstance(_sub, APIRouter):
        router.include_router(_sub)
