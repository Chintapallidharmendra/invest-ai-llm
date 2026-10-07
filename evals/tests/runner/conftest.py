"""Fixtures for the eval runner tests (Story 1.8).

Run from the repo root: ``uv run --project backend pytest evals/tests/runner``.
The corpus build is generated once if missing; the fake LLM comes from Story 1.6.
"""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT, REPO_ROOT / "backend", REPO_ROOT / "evals" / "corpus"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from tests.fakes.fake_llm import FakeLLM  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def corpus_build() -> Path:
    from evals.runner import corpus  # noqa: PLC0415

    if not (corpus.BUILD_DIR / "invoices_nilgiri_logistics.xlsx").is_file():
        from generate.build import generate  # noqa: PLC0415

        generate(corpus.BUILD_DIR)
    return corpus.BUILD_DIR


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
