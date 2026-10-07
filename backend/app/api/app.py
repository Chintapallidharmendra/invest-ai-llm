"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.api.middleware import CorrelationIdMiddleware
from app.api.router import API_PREFIX, build_api_router
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.errors import install_exception_handlers
from app.core.obs import configure_logging


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def create_app(*, configure_logs: bool = True) -> FastAPI:
    if configure_logs:
        configure_logging(get_settings().log_level)

    app = FastAPI(
        title="invest-ai-llm",
        version="0.1.0",
        openapi_url=f"{API_PREFIX}/openapi.json",
        # The interactive docs load assets from a CDN: no egress, strict CSP.
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan,
    )
    install_exception_handlers(app)
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(health.router)
    app.include_router(build_api_router())
    return app
