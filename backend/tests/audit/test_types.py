"""AC #2, #3, #6 without a database: types, canonical JSON, hashing, the CI check,
the registry and the canary helper."""

import hashlib
import hmac
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, ClassVar

import pytest
from pydantic import StringConstraints, ValidationError

from app.audit import check_models, registry
from app.audit import types as audit_types
from app.audit.chain import ZERO_HASH, ChainRow, canonical_json, payload_of, to_json_value
from app.audit.testing import assert_no_content
from app.audit.types import AuditEvent, AuditKeyError, KeyedHash, VersionStr
from tests.audit.conftest import CANARY, KEY, dummy_event
from tests.audit.events import Colour, DummyEvent, Level
from tests.conftest import BACKEND_DIR

pytestmark = pytest.mark.usefixtures("audit_key")


# --- VersionStr / KeyedHash --------------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["3", "v1", "1.2", "v1.2.3", "2.0.1-rc1", "1.4+build.7", "1.0-beta.2", "10.20.30.40"]
)
def test_version_str_accepts_versions(value: str) -> None:
    assert VersionStr.validate(value) == value


@pytest.mark.parametrize(
    "value",
    ["", "v", "latest", "1.0-falcon", "deal-v2", "1.2.3.4.5", "1.0 beta", "Project Falcon", "x1"],
)
def test_version_str_rejects_words(value: str) -> None:
    with pytest.raises(ValueError, match="VersionStr"):
        VersionStr.validate(value)


def test_keyed_hash_is_a_stable_hmac_unlike_plain_sha256() -> None:
    digest = KeyedHash.of("Acme Industries")
    assert digest == KeyedHash.of("Acme Industries")
    assert digest == hmac.new(KEY, b"Acme Industries", hashlib.sha256).hexdigest()
    assert digest != hashlib.sha256(b"Acme Industries").hexdigest()
    assert KeyedHash.of(b"Acme Industries") == digest
    assert KeyedHash.of("Acme Industries.") != digest
    assert KeyedHash.pattern.fullmatch(digest)


def test_keyed_hash_depends_on_the_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = KeyedHash.of("x")
    other = tmp_path / "other_key"
    other.write_bytes(b"another-key-of-at-least-32-bytes-long!!")
    monkeypatch.setenv("APP_AUDIT_HMAC_KEY_PATH", str(other))
    audit_types.get_audit_settings.cache_clear()
    audit_types.audit_key.cache_clear()
    assert KeyedHash.of("x") != first


@pytest.mark.parametrize("content", [None, b"short-key"])
def test_a_missing_or_short_key_fails_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: bytes | None
) -> None:
    path = tmp_path / "key"
    if content is not None:
        path.write_bytes(content)
    monkeypatch.setenv("APP_AUDIT_HMAC_KEY_PATH", str(path))
    audit_types.get_audit_settings.cache_clear()
    audit_types.audit_key.cache_clear()
    with pytest.raises(AuditKeyError):
        KeyedHash.of("x")


def test_models_validate_their_types() -> None:
    with pytest.raises(ValidationError):
        dummy_event(model_version="latest")
    with pytest.raises(ValidationError):
        dummy_event(name_hash="not-a-hash")
    with pytest.raises(ValidationError):
        dummy_event(extra_field=1)  # extra="forbid"


# --- Canonical JSON and hashing ------------------------------------------------------------


def test_canonical_json_is_sorted_compact_ascii() -> None:
    assert canonical_json({"b": 1, "a": [True, None], "c": "é"}) == (
        b'{"a":[true,null],"b":1,"c":"\\u00e9"}'
    )


def test_payload_values_are_normalised() -> None:
    payload = payload_of(dummy_event())
    assert payload == {
        "object_id": "00000000-0000-7000-8000-000000000001",
        "colour": "red",
        "level": 2,
        "count": 3,
        "flag": True,
        "at": "2026-10-08T09:30:15.123456Z",
        "took": 1_000_500,
        "model_version": "2.0.1-rc1",
        "name_hash": KeyedHash.of("Acme Industries"),
        "maybe_id": None,
    }
    with pytest.raises(ValueError, match="timezone-aware"):
        to_json_value(datetime(2026, 1, 1))
    with pytest.raises(TypeError):
        to_json_value(1.5)


def test_hash_is_sha256_of_prev_hash_and_canonical_row() -> None:
    row = ChainRow(
        id=uuid.UUID("00000000-0000-7000-8000-000000000009"),
        seq=1,
        occurred_at=datetime(2026, 10, 8, tzinfo=UTC) + timedelta(microseconds=7),
        correlation_id=uuid.UUID("00000000-0000-7000-8000-0000000000cc"),
        actor_user_id=None,
        event_type="testaudit.tiny",
        target_type=None,
        target_id=None,
        priority="normal",
        payload={"n": 1},
        prev_hash=ZERO_HASH,
    )
    expected_json = (
        b'{"actor_user_id":null,"correlation_id":"00000000-0000-7000-8000-0000000000cc",'
        b'"event_type":"testaudit.tiny","id":"00000000-0000-7000-8000-000000000009",'
        b'"occurred_at":"2026-10-08T00:00:00.000007Z","payload":{"n":1},'
        b'"prev_hash":"' + b"00" * 32 + b'","priority":"normal","seq":1,'
        b'"target_id":null,"target_type":null}'
    )
    assert row.material() == expected_json
    assert row.compute_hash() == hashlib.sha256(ZERO_HASH + expected_json).digest()


# --- check_models (the CI check) ---------------------------------------------------------


class _Plain(Enum):
    A = 1.5


@pytest.mark.parametrize(
    ("annotation", "fragment"),
    [
        (str, "free-text str"),
        (str | None, "free-text str"),
        (Annotated[str, StringConstraints(pattern="^[a-z]+$")], "free-text str"),
        (float, "float is not an allowed"),
        (bytes, "bytes is not an allowed"),
        (list[uuid.UUID], "not an allowed"),
        (dict[str, int], "not an allowed"),
        (Any, "not an allowed"),
        (int | uuid.UUID, "unions"),
        (_Plain, "StrEnum or IntEnum"),
        (Annotated[int, "meta"], "annotated"),
    ],
)
def test_check_models_rejects_free_text_and_other_types(annotation: Any, fragment: str) -> None:
    namespace: dict[str, Any] = {"__annotations__": {"field": annotation}}
    model = type("Bad", (AuditEvent,), namespace)
    assert any(fragment in problem for problem in check_models.violations(model))


def test_check_models_accepts_every_allowed_type() -> None:
    assert check_models.violations(DummyEvent) == []


def test_a_registered_free_text_model_fails_the_check(capsys: pytest.CaptureFixture[str]) -> None:
    class NoteAdded(AuditEvent):
        event_type: ClassVar[str] = "testaudit.note_added"
        note: str

    try:
        assert check_models.main([]) == 1
        out = capsys.readouterr().out
        assert "NoteAdded (testaudit.note_added) note: free-text str" in out
    finally:
        registry.unregister("testaudit.note_added")
    assert check_models.main([]) == 0


def test_check_models_cli_passes_on_the_codebase() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "app.audit.check_models"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


# --- Registry ------------------------------------------------------------------------------


def test_registry_discovers_module_event_files() -> None:
    assert "app.audit.audit_events" in registry.discover()
    assert registry.get("audit.verified") is not None
    assert registry.get("testaudit.dummy") is DummyEvent


def test_registry_rejects_bad_and_duplicate_event_types() -> None:
    with pytest.raises(ValueError, match=r"<domain>\.<action>"):

        class BadName(AuditEvent):
            event_type: ClassVar[str] = "NoDot"

    with pytest.raises(ValueError, match="already registered"):

        class Duplicate(AuditEvent):
            event_type: ClassVar[str] = "testaudit.dummy"

    assert registry.get("testaudit.dummy") is DummyEvent


# --- assert_no_content (AC #6) ----------------------------------------------------------------


def test_assert_no_content_passes_for_metadata_payloads() -> None:
    assert_no_content(dummy_event())
    assert_no_content(payload_of(dummy_event(maybe_id=uuid.uuid4())))
    assert_no_content({"colour": Colour.GREEN.value, "level": Level.LOW.value, "n": None})


@pytest.mark.parametrize(
    "payload",
    [
        {"colour": "red", "note": CANARY},
        {"name": "Acme Industries"},
        {"file": "term-sheet-v3.pdf"},
        {"ratio": 1.5},
        {"nested": {"id": "00000000-0000-7000-8000-000000000001"}},
        {"ids": ["00000000-0000-7000-8000-000000000001"]},
        {"upper_uuid": "00000000-0000-7000-8000-00000000000A"},
    ],
)
def test_assert_no_content_fails_for_content(payload: dict[str, Any]) -> None:
    with pytest.raises(AssertionError) as exc:
        assert_no_content(payload)
    assert CANARY not in str(exc.value)  # the message never echoes the value
