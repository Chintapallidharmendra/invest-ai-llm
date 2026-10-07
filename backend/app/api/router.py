"""API router assembly with auto-discovery.

Every ``app/<module>/routes.py`` that exposes ``router: APIRouter`` is mounted under
``/api/v1``, so feature stories never edit a shared router list.
"""

import importlib
import importlib.util
import pkgutil
from typing import Final

from fastapi import APIRouter

import app

API_PREFIX: Final = "/api/v1"


def discover_routers() -> list[tuple[str, APIRouter]]:
    """Return ``(module_name, router)`` for each ``app.<module>.routes``, sorted by name."""
    found: list[tuple[str, APIRouter]] = []
    for info in sorted(pkgutil.iter_modules(app.__path__), key=lambda m: m.name):
        if not info.ispkg:
            continue
        name = f"app.{info.name}.routes"
        if importlib.util.find_spec(name) is None:
            continue
        router = getattr(importlib.import_module(name), "router", None)
        if isinstance(router, APIRouter):
            found.append((name, router))
    return found


def build_api_router() -> APIRouter:
    api_router = APIRouter(prefix=API_PREFIX)
    for _, router in discover_routers():
        api_router.include_router(router)
    return api_router
