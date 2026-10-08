"""The key-encryption key (ADR-028) and the AES-256-GCM sealing used at every level.

The KEK is loaded from ``APP_CRYPTO_KEK_PATH`` (``/run/secrets/kek``) into a :class:`Kek`
object that can wrap and unwrap but never hands out its bytes: no function returns the
KEK, nothing logs it, nothing writes it to the database. A missing or malformed KEK
fails the ``kek_loaded`` readiness check, so the system refuses work (NFR-013).

Sealed format (wrapped keys and fields): ``version(1) | nonce(12) | ciphertext | tag(16)``.
"""

import base64
import binascii
import os
from functools import lru_cache
from pathlib import Path
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core import readiness
from app.crypto.settings import CryptoSettings, get_crypto_settings

KEY_BYTES: Final = 32
NONCE_BYTES: Final = 12
TAG_BYTES: Final = 16
FORMAT_VERSION: Final = 1
SEALED_OVERHEAD: Final = 1 + NONCE_BYTES + TAG_BYTES
READINESS_CHECK: Final = "kek_loaded"


class CryptoError(Exception):
    """Base of the crypto module's errors."""


class IntegrityError(CryptoError):
    """Ciphertext failed authentication: tampered, truncated, or bound to other AAD."""


class KekError(CryptoError):
    """The KEK file is missing, unreadable or not a 256-bit key."""


def random_key() -> bytes:
    return os.urandom(KEY_BYTES)


def seal(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    return bytes([FORMAT_VERSION]) + nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def open_sealed(key: bytes, sealed: bytes, aad: bytes) -> bytes:
    if len(sealed) < SEALED_OVERHEAD or sealed[0] != FORMAT_VERSION:
        raise IntegrityError("not a sealed value of a known format version")
    nonce, body = sealed[1 : 1 + NONCE_BYTES], sealed[1 + NONCE_BYTES :]
    try:
        return AESGCM(key).decrypt(nonce, body, aad)
    except InvalidTag:
        raise IntegrityError("authentication failed") from None


def parse_kek(data: bytes) -> bytes:
    """32 raw bytes, 64 hex digits, or base64 of 32 bytes (surrounding whitespace allowed
    for the text forms). Raises :class:`KekError` naming the problem, never the bytes."""
    if len(data) == KEY_BYTES:
        return data
    text = data.strip()
    if len(text) == 2 * KEY_BYTES:
        try:
            return bytes.fromhex(text.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            pass
    try:
        decoded = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        decoded = b""
    if len(decoded) == KEY_BYTES:
        return decoded
    raise KekError("the KEK must be 32 bytes (raw, 64 hex digits or base64)")


class Kek:
    """A loaded KEK. Wraps and unwraps space keys; its bytes are never exposed."""

    __slots__ = ("_aead_key", "version")

    def __init__(self, key: bytes, version: int) -> None:
        if len(key) != KEY_BYTES:
            raise KekError("the KEK must be 32 bytes")
        self._aead_key = key
        self.version = version

    def __repr__(self) -> str:
        return f"Kek(version={self.version})"

    def __reduce__(self) -> tuple[object, ...]:
        raise TypeError("a Kek cannot be pickled")

    def wrap(self, key: bytes, aad: bytes) -> bytes:
        return seal(self._aead_key, key, aad)

    def unwrap(self, wrapped: bytes, aad: bytes) -> bytes:
        return open_sealed(self._aead_key, wrapped, aad)

    @classmethod
    def from_file(cls, path: Path, version: int) -> "Kek":
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise KekError(f"KEK unreadable ({type(exc).__name__}): {path}") from None
        try:
            return cls(parse_kek(data), version)
        except KekError as exc:
            raise KekError(f"{exc}: {path}") from None


@lru_cache(maxsize=1)
def _load(path: Path, version: int) -> Kek:
    return Kek.from_file(path, version)


def get_kek(settings: CryptoSettings | None = None) -> Kek:
    """The process KEK (loaded once). Raises :class:`KekError` if it can't be loaded."""
    settings = settings or get_crypto_settings()
    return _load(settings.kek_path, settings.kek_version)


def reset_kek() -> None:
    """Forget the loaded KEK (after rotation and in tests)."""
    _load.cache_clear()


async def _kek_loaded() -> bool:
    get_kek()  # raises (and so fails the check) if missing or malformed
    return True


def register_checks() -> None:
    """Add ``kek_loaded`` to ``/readyz``. Idempotent; call at API and worker start-up."""
    readiness.unregister_check(READINESS_CHECK)
    readiness.register_check(READINESS_CHECK, _kek_loaded)


def unregister_checks() -> None:
    readiness.unregister_check(READINESS_CHECK)
