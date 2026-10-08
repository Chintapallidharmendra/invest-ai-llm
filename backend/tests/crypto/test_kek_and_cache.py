"""AC #1 (KEK loading, readiness, no leaks) and AC #6's cache (TTL, size bound)."""

import base64
import io
import os
import pickle
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core import obs, readiness
from app.crypto import kek as kek_module
from app.crypto.cache import KeyCache
from app.crypto.kek import READINESS_CHECK, IntegrityError, Kek, KekError, get_kek, parse_kek
from app.crypto.settings import CryptoSettings
from tests.crypto.conftest import reset_crypto

KEY = bytes(range(32))


# --- Parsing -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        KEY,
        KEY.hex().encode(),
        KEY.hex().encode() + b"\n",  # openssl rand -hex 32 (the runbook)
        KEY.hex().upper().encode(),
        base64.b64encode(KEY),
        base64.b64encode(KEY) + b"\n",
    ],
    ids=["raw", "hex", "hex-newline", "hex-upper", "base64", "base64-newline"],
)
def test_kek_formats(data: bytes) -> None:
    assert parse_kek(data) == KEY


@pytest.mark.parametrize(
    "data",
    [b"", KEY[:31], KEY + b"x", base64.b64encode(KEY[:16]), b"g" * 64, b"not base64 at all!!"],
    ids=["empty", "31-bytes", "33-bytes", "base64-16", "bad-hex", "garbage"],
)
def test_wrong_length_or_format_is_rejected(data: bytes) -> None:
    with pytest.raises(KekError, match="32 bytes"):
        parse_kek(data)


# --- Readiness -------------------------------------------------------------------------------


@pytest.fixture
def check_registered() -> Iterator[None]:
    kek_module.register_checks()
    kek_module.register_checks()  # idempotent
    yield
    kek_module.unregister_checks()


@pytest.fixture
def logs() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    obs.configure_logging("DEBUG", stream=stream)
    yield stream
    obs.configure_logging("INFO", stream=sys.stdout)


def _point_at(path: Path, monkeypatch: pytest.MonkeyPatch, version: int = 1) -> None:
    monkeypatch.setenv("APP_CRYPTO_KEK_PATH", str(path))
    monkeypatch.setenv("APP_CRYPTO_KEK_VERSION", str(version))
    reset_crypto()


@pytest.mark.usefixtures("check_registered")
@pytest.mark.parametrize("content", [None, b"\x00" * 16, b"short-secret-kek-value"])
async def test_missing_or_short_kek_fails_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, logs: io.StringIO, content: bytes | None
) -> None:
    path = tmp_path / "kek"
    if content is not None:
        path.write_bytes(content)
    _point_at(path, monkeypatch)
    assert READINESS_CHECK in await readiness.failing_checks()
    with pytest.raises(KekError):
        get_kek()
    if content:
        assert content.decode(errors="ignore") not in logs.getvalue()
    reset_crypto()


@pytest.mark.usefixtures("check_registered")
async def test_a_valid_kek_passes_readiness_and_never_appears_in_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, logs: io.StringIO
) -> None:
    secret = os.urandom(32)
    path = tmp_path / "kek"
    path.write_bytes(secret.hex().encode() + b"\n")
    _point_at(path, monkeypatch, version=3)
    assert READINESS_CHECK not in await readiness.failing_checks()
    kek = get_kek()
    assert kek.version == 3
    output = logs.getvalue()
    for form in (secret.hex(), secret.hex().upper(), base64.b64encode(secret).decode()):
        assert form not in output
    assert secret not in output.encode()
    reset_crypto()


def test_the_kek_object_never_exposes_its_bytes() -> None:
    kek = Kek(KEY, 1)
    assert repr(kek) == "Kek(version=1)"
    assert KEY.hex() not in repr(kek)
    public = [name for name in dir(kek) if not name.startswith("_")]
    assert all(not isinstance(getattr(kek, name), bytes) for name in public)
    with pytest.raises(TypeError, match="pickled"):
        pickle.dumps(kek)
    wrapped = kek.wrap(b"k" * 32, b"aad")
    assert KEY not in wrapped
    assert kek.unwrap(wrapped, b"aad") == b"k" * 32
    with pytest.raises(IntegrityError):
        kek.unwrap(wrapped, b"other aad")
    with pytest.raises(IntegrityError):
        Kek(os.urandom(32), 1).unwrap(wrapped, b"aad")


def test_errors_name_the_path_not_the_key(tmp_path: Path) -> None:
    path = tmp_path / "kek"
    path.write_bytes(b"SECRET-but-too-short")
    with pytest.raises(KekError) as exc:
        Kek.from_file(path, 1)
    assert str(path) in str(exc.value)
    assert "SECRET" not in str(exc.value)


# --- Cache ------------------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_cache_entries_expire_after_the_ttl() -> None:
    clock = FakeClock()
    cache = KeyCache(ttl_s=300, max_entries=10, clock=clock)
    space = uuid.uuid4()
    cache.put(("space", space), b"k", space)
    clock.now = 299.9
    assert cache.get(("space", space)) == b"k"
    clock.now = 300.0
    assert cache.get(("space", space)) is None
    assert len(cache) == 0


def test_cache_is_size_bounded_lru() -> None:
    cache = KeyCache(ttl_s=300, max_entries=3)
    space = uuid.uuid4()
    for i in range(3):
        cache.put(("object", "doc", i), bytes([i]), space)
    cache.get(("object", "doc", 0))  # touch: 1 is now the least recently used
    cache.put(("object", "doc", 3), b"\x03", space)
    assert len(cache) == 3
    assert cache.get(("object", "doc", 1)) is None
    assert cache.get(("object", "doc", 0)) == b"\x00"


def test_evict_space_drops_its_space_and_object_keys_only() -> None:
    cache = KeyCache(ttl_s=300, max_entries=10)
    a, b = uuid.uuid4(), uuid.uuid4()
    cache.put(("space", a), b"a", a)
    cache.put(("object", "doc", 1), b"a1", a)
    cache.put(("object", "doc", 2), b"b2", b)
    cache.evict_space(a)
    assert cache.get(("space", a)) is None
    assert cache.get(("object", "doc", 1)) is None
    assert cache.get(("object", "doc", 2)) == b"b2"


def test_cache_ttl_is_capped_at_five_minutes() -> None:
    with pytest.raises(ValidationError):
        CryptoSettings(key_cache_ttl_s=301)
    assert CryptoSettings().key_cache_ttl_s <= 300
