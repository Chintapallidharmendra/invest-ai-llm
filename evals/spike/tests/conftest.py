"""Fixtures for the spike tests (Story 1.9). Run from the repo root:
``uv run --project backend pytest evals/spike`` (``evals/spike/pytest.ini`` turns on
``asyncio_mode = auto``). The fake LLM comes from Story 1.6."""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT, REPO_ROOT / "backend"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from tests.fakes.fake_llm import FakeLLM  # noqa: E402


@pytest.fixture(scope="session")
def _fake_server() -> Iterator[FakeLLM]:
    server = FakeLLM().start()
    yield server
    server.stop()


@pytest.fixture
def fake_llm(_fake_server: FakeLLM) -> Iterator[FakeLLM]:
    _fake_server.reset()
    yield _fake_server
    _fake_server.reset()
