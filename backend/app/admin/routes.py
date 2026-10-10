"""Admin API. Mounts every ``app/admin/routes_*.py`` router as is, so each admin area
(users, settings, ...) lives in a file of its own. Every admin router guards its routes
with ``require_role(Role.ADMIN)``."""

import importlib
import pkgutil

from fastapi import APIRouter

import app.admin


def discover_subrouters() -> list[str]:
    """Names of the ``app.admin.routes_*`` modules."""
    return [
        f"app.admin.{info.name}"
        for info in sorted(pkgutil.iter_modules(app.admin.__path__), key=lambda m: m.name)
        if info.name.startswith("routes_") and not info.ispkg
    ]


router = APIRouter()
for _name in discover_subrouters():
    _sub = getattr(importlib.import_module(_name), "router", None)
    if isinstance(_sub, APIRouter):
        router.include_router(_sub)
