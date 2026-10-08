"""AC #5: offline model loading that fails fast, and no values in logs."""

import io
import json
import logging
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import spacy
import spacy.cli

from app.core import obs
from app.guardrails.identifiers import engine as engine_module
from app.guardrails.identifiers.engine import IdentifierEngine, SpacyModelError, load_spacy_model
from app.guardrails.identifiers.settings import IdentifierSettings
from tests.guardrails.identifiers.cases import VISA, A


@pytest.fixture
def no_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("spaCy tried to download a model")

    monkeypatch.setattr(spacy.cli, "download", refuse)


@pytest.mark.usefixtures("no_downloads")
def test_missing_model_path_raises_at_init(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(SpacyModelError, match="not found") as exc:
        IdentifierEngine(IdentifierSettings(spacy_model_path=missing))
    assert str(missing) in str(exc.value)
    assert "APP_GUARDRAILS_SPACY_MODEL_PATH" in str(exc.value)


@pytest.mark.usefixtures("no_downloads")
def test_a_directory_without_a_model_raises(tmp_path: Path) -> None:
    (tmp_path / "config.cfg").write_text("[nlp]\nlang = ???\n")
    with pytest.raises(SpacyModelError, match="unreadable"):
        load_spacy_model(tmp_path)


def test_missing_components_raise(spacy_model_path: Path) -> None:
    with pytest.raises(SpacyModelError, match="no component"):
        load_spacy_model(spacy_model_path, ["lemmatizer_that_does_not_exist"])


def test_only_the_tokenizer_is_kept_by_default(spacy_model_path: Path) -> None:
    assert load_spacy_model(spacy_model_path).pipe_names == []


def test_env_var_names_the_model_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APP_GUARDRAILS_SPACY_MODEL_PATH", str(tmp_path / "m"))
    assert IdentifierSettings().spacy_model_path == tmp_path / "m"


@pytest.mark.usefixtures("no_downloads")
def test_get_engine_fails_fast_on_a_missing_model(
    module_engine: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("APP_GUARDRAILS_SPACY_MODEL_PATH", str(tmp_path / "absent"))
    engine_module.get_identifier_settings.cache_clear()
    with pytest.raises(SpacyModelError):
        engine_module.get_engine()


@pytest.fixture
def captured_logs() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    obs.configure_logging("DEBUG", stream=stream)
    presidio = logging.getLogger("presidio-analyzer")
    old_level = presidio.level
    presidio.setLevel(logging.DEBUG)
    yield stream
    presidio.setLevel(old_level)
    obs.configure_logging("INFO", stream=sys.stdout)


def test_logs_never_contain_matched_values(
    spacy_model_path: Path, captured_logs: io.StringIO
) -> None:
    planted = ["ABCPD1234E", A[0], VISA, "ravi@okaxis", "J8369854", "IN30123456789012"]
    text = (
        f"PAN {planted[0]} aadhaar {planted[1]} card {planted[2]} upi {planted[3]} "
        f"passport {planted[4]} demat {planted[5]}"
    )
    engine = IdentifierEngine(IdentifierSettings(spacy_model_path=spacy_model_path))
    found = engine.detect(text)
    engine.mask(text)
    engine.detect_batch([text, text])
    engine.mask_batch([text])
    assert len(found) == len(planted)

    output = captured_logs.getvalue()
    assert output  # DEBUG logging was live (Presidio and the engine both log)
    for value in planted:
        assert value not in output
    events = [json.loads(line) for line in output.splitlines() if line.strip()]
    ready = [e for e in events if e.get("event") == "identifiers.ready"]
    assert ready
    assert ready[0]["recognizer_count"] > 0
