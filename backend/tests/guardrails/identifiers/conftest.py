"""Fixtures for the identifier library tests.

The spaCy model comes from ``APP_GUARDRAILS_SPACY_MODEL_PATH`` when it is set (e.g. the
real ``en_core_web_sm``); otherwise a blank English pipeline is written to a temporary
directory. The engine keeps only the tokenizer either way, so results are identical.

Valid test numbers are generated with :func:`verhoeff_complete` and :func:`luhn_complete`
rather than hard-coded, so a typo can't silently turn a positive case into a negative.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import spacy

from app.guardrails.identifiers import engine as engine_module
from app.guardrails.identifiers.engine import IdentifierEngine
from app.guardrails.identifiers.settings import IdentifierSettings, get_identifier_settings
from app.guardrails.identifiers.types import Finding, IdentifierType

MODEL_ENV = "APP_GUARDRAILS_SPACY_MODEL_PATH"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "perf: micro-benchmark with a latency/throughput bar")


@pytest.fixture(scope="session")
def spacy_model_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    configured = os.environ.get(MODEL_ENV)
    if configured:
        return Path(configured)
    path = tmp_path_factory.mktemp("spacy") / "en_blank"
    spacy.blank("en").to_disk(path)
    return path


@pytest.fixture(scope="session")
def engine(spacy_model_path: Path) -> IdentifierEngine:
    return IdentifierEngine(IdentifierSettings(spacy_model_path=spacy_model_path))


@pytest.fixture
def module_engine(spacy_model_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Points the module-level ``detect``/``mask`` (``get_engine()``) at the test model."""
    monkeypatch.setenv(MODEL_ENV, str(spacy_model_path))
    get_identifier_settings.cache_clear()
    engine_module.get_engine.cache_clear()
    yield
    get_identifier_settings.cache_clear()
    engine_module.get_engine.cache_clear()


# --- Checksums ----------------------------------------------------------------------

_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)
_INV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def verhoeff_valid(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_complete(base: str) -> str:
    """``base`` (11 digits) plus its Verhoeff check digit."""
    c = 0
    for i, ch in enumerate(reversed(base)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    full = base + str(_INV[c])
    assert verhoeff_valid(full)
    return full


def verhoeff_invalid(base: str) -> str:
    """``base`` (11 digits) plus a wrong check digit."""
    valid = verhoeff_complete(base)
    return valid[:-1] + str((int(valid[-1]) + 1) % 10)


def luhn_valid(number: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def luhn_complete(base: str) -> str:
    full = next(base + str(d) for d in range(10) if luhn_valid(base + str(d)))
    return full


def luhn_invalid(base: str) -> str:
    valid = luhn_complete(base)
    return valid[:-1] + str((int(valid[-1]) + 1) % 10)


def group(number: str, sizes: tuple[int, ...], sep: str = " ") -> str:
    parts, i = [], 0
    for size in sizes:
        parts.append(number[i : i + size])
        i += size
    return sep.join(parts)


def spans(findings: list[Finding]) -> list[tuple[IdentifierType, int, int]]:
    return [(f.type, f.start, f.end) for f in findings]
