from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.app import create_app
from app.core.errors import ProblemException
from tests.core.conftest import LogCapture, logs

__all__ = ["logs"]


class Deal(BaseModel):
    name: str
    size_crore: int


def _add_test_routes(app: FastAPI) -> None:
    @app.get("/test/problem")
    async def problem() -> None:
        raise ProblemException(409, "deal_locked", "Deal is locked", "Try later", retry_after_s=5)

    @app.post("/test/deals")
    async def create_deal(deal: Deal) -> Deal:
        return deal

    @app.get("/test/boom")
    async def boom() -> None:
        raise ValueError("secret text")


@pytest.fixture
def client(logs: LogCapture) -> Iterator[TestClient]:
    app = create_app(configure_logs=False)
    _add_test_routes(app)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
