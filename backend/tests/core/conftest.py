import io
import json
import os
import sys
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

from app.core import db, obs
from app.core.config import get_settings

TEST_DB_ENV = "APP_TEST_DATABASE_URL"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", f"db: needs PostgreSQL 18 + pgvector (set {TEST_DB_ENV})")


@pytest.fixture
def test_database_url() -> str:
    url = os.environ.get(TEST_DB_ENV)
    if not url:
        pytest.skip(f"{TEST_DB_ENV} not set (Postgres fixture arrives in Story 1.6)")
    return url


@pytest.fixture
async def database(test_database_url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    monkeypatch.setenv("APP_DATABASE_URL", test_database_url)
    get_settings.cache_clear()
    await db.dispose_engine()
    yield test_database_url
    await db.dispose_engine()
    get_settings.cache_clear()


class LogCapture:
    """Captures the fully processed JSON log output."""

    def __init__(self) -> None:
        self.stream = io.StringIO()

    @property
    def text(self) -> str:
        return self.stream.getvalue()

    @property
    def events(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.text.splitlines() if line.strip()]


@pytest.fixture
def logs() -> Iterator[LogCapture]:
    capture = LogCapture()
    obs.configure_logging("DEBUG", stream=capture.stream)
    yield capture
    obs.configure_logging("INFO", stream=sys.stdout)
