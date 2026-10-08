"""Password policy (ADR-006; NIST SP 800-63B): length and breached list, no composition rules.

``validate(password, username)`` returns every failed rule as a :class:`ReasonCode`
(empty list = acceptable). Checks run on the NFKC-normalised password, compared
case-insensitively (``casefold``).

The breached list is loaded once per path into memory. A missing or unreadable file
raises :class:`BreachedListError`; call :func:`preload` at process start-up so that
happens before any request.
"""

import unicodedata
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from app.auth.settings import AuthSettings, get_auth_settings


class ReasonCode(StrEnum):
    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"
    BREACHED = "breached"
    CONTAINS_USERNAME = "contains_username"


class BreachedListError(RuntimeError):
    """The bundled breached-password list could not be loaded."""


def _fold(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


@lru_cache(maxsize=4)
def load_breached_list(path: Path) -> frozenset[str]:
    """Every entry of the list at ``path``, NFKC-normalised and casefolded."""
    try:
        with path.open(encoding="utf-8", errors="strict") as fh:
            entries = frozenset(_fold(line.rstrip("\r\n")) for line in fh)
    except FileNotFoundError:
        raise BreachedListError(f"breached-password list not found: {path}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise BreachedListError(
            f"breached-password list unreadable ({type(exc).__name__}): {path}"
        ) from None
    entries = entries - {""}
    if not entries:
        raise BreachedListError(f"breached-password list is empty: {path}")
    return entries


def preload(settings: AuthSettings | None = None) -> int:
    """Load the list now (fail fast at start-up); returns the number of entries."""
    settings = settings or get_auth_settings()
    return len(load_breached_list(settings.breached_passwords_path))


def validate(
    password: str, username: str, *, settings: AuthSettings | None = None
) -> list[ReasonCode]:
    settings = settings or get_auth_settings()
    normalised = unicodedata.normalize("NFKC", password)
    folded = normalised.casefold()
    reasons: list[ReasonCode] = []

    length = len(normalised)  # code points, not bytes
    if length < settings.password_min_length:
        reasons.append(ReasonCode.TOO_SHORT)
    elif length > settings.password_max_length:
        reasons.append(ReasonCode.TOO_LONG)

    if folded in load_breached_list(settings.breached_passwords_path):
        reasons.append(ReasonCode.BREACHED)

    name = _fold(username).strip()
    if name and name in folded:
        reasons.append(ReasonCode.CONTAINS_USERNAME)
    return reasons
