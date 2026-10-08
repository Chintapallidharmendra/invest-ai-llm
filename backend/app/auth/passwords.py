"""Password hashing with Argon2id via pwdlib (ADR-006).

pwdlib's Argon2 defaults (t=3, m=64 MiB, p=4) meet or exceed the OWASP recommendation.
Passwords are NFKC-normalised before hashing and verifying, so the same passphrase typed
with different Unicode compositions still matches.

For unknown usernames, call :func:`verify_dummy` so the response takes as long as a
real verification (no user enumeration by timing).
"""

import unicodedata
from functools import lru_cache
from typing import Final

from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher

# A fixed, non-secret input: the dummy hash only exists to spend the same CPU time.
_DUMMY_PASSWORD: Final = "dummy-password-for-timing-equalisation"  # noqa: S105 (not a secret)


def normalise(password: str) -> str:
    return unicodedata.normalize("NFKC", password)


@lru_cache
def _hasher() -> PasswordHash:
    return PasswordHash((Argon2Hasher(),))


@lru_cache
def _dummy_hash() -> str:
    return _hasher().hash(_DUMMY_PASSWORD)


def hash_password(password: str) -> str:
    """An Argon2id PHC string (``$argon2id$v=19$m=…``)."""
    return _hasher().hash(normalise(password))


def verify_password(password: str, password_hash: str) -> bool:
    """True if ``password`` matches; False for a wrong password or an unknown hash format."""
    try:
        return _hasher().verify(normalise(password), password_hash)
    except UnknownHashError:
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the hash uses weaker parameters (or another scheme) than the current ones."""
    hasher = _hasher().hashers[0]
    if not hasher.identify(password_hash):
        return True
    return hasher.check_needs_rehash(password_hash)


def verify_and_rehash(password: str, password_hash: str) -> tuple[bool, str | None]:
    """Verify, and return a fresh hash when the stored one should be upgraded."""
    if not verify_password(password, password_hash):
        return False, None
    return True, (hash_password(password) if needs_rehash(password_hash) else None)


def verify_dummy(password: str) -> bool:
    """Spend one verification's worth of time; always False. Use for unknown users."""
    _hasher().verify(normalise(password), _dummy_hash())
    return False
