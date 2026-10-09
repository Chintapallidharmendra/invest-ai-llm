"""FastAPI application factory."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.api.middleware import CorrelationIdMiddleware
from app.api.router import API_PREFIX, build_api_router
from app.auth import policy
from app.auth.csrf import CsrfMiddleware
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.errors import install_exception_handlers
from app.core.obs import configure_logging
from app.crypto import kek
from app.llm import readiness as llm_readiness


def _lifespan(readiness_checks: bool) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Fail start-up without the breached-password list (ADR-006).
        policy.preload()
        if readiness_checks:
            kek.register_checks()
            llm_readiness.register_checks()
        try:
            yield
        finally:
            if readiness_checks:
                kek.unregister_checks()
                llm_readiness.unregister_checks()
            await dispose_engine()

    return lifespan


def create_app(*, configure_logs: bool = True, readiness_checks: bool = True) -> FastAPI:
    """The API app. ``readiness_checks=False`` is for tests that drive ``/readyz``
    with their own check registry."""
    if configure_logs:
        configure_logging(get_settings().log_level)

    app = FastAPI(
        title="invest-ai-llm",
        version="0.1.0",
        openapi_url=f"{API_PREFIX}/openapi.json",
        # The interactive docs load assets from a CDN: no egress, strict CSP.
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan(readiness_checks),
    )
    install_exception_handlers(app)
    # Added first so it runs inside the correlation middleware (its 403s carry the ID).
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(health.router)
    app.include_router(build_api_router())
    return app
