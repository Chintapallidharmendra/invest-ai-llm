"""Fixtures for the corpus tests: one shared build per session (about 5 s)."""

import sys
from pathlib import Path

import pytest

CORPUS_DIR = Path(__file__).resolve().parents[1]
if str(CORPUS_DIR) not in sys.path:
    sys.path.insert(0, str(CORPUS_DIR))

from generate.build import generate  # noqa: E402
from generate.manifest_schema import Manifest  # noqa: E402


@pytest.fixture(scope="session")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Manifest]:
    out = tmp_path_factory.mktemp("corpus") / "build"
    return out, generate(out)


@pytest.fixture(scope="session")
def build_dir(built: tuple[Path, Manifest]) -> Path:
    return built[0]


@pytest.fixture(scope="session")
def manifest(built: tuple[Path, Manifest]) -> Manifest:
    return built[1]
