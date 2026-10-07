"""Liveness, readiness and metrics, mounted at the root (not under /api/v1)."""

from http import HTTPStatus

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core import readiness
from app.core.errors import Problem, ProblemException

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/readyz",
    responses={HTTPStatus.SERVICE_UNAVAILABLE: {"model": Problem, "description": "Not ready"}},
)
async def readyz() -> dict[str, str]:
    failing = await readiness.failing_checks()
    if failing:
        raise ProblemException(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "not_ready",
            "Service not ready",
            failing_checks=failing,
        )
    return {"status": "ready"}


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
