"""Per-user prefix-cache salts (ADR-031; FR-041).

``cache_salt = base64url(HMAC-SHA256(salt_secret, salt_subject))``, unpadded (43 chars).
vLLM mixes the salt into prefix-cache block hashes, so one user's prompts never warm or
reveal another user's cache. The salt is derived, never stored, and is not reversible
to the user ID without the secret.
"""

import base64
import hashlib
import hmac
from uuid import UUID

from pydantic import SecretStr

from app.llm.errors import MissingCacheSalt


def subject_key(salt_subject: str | UUID | None) -> str:
    """Validate and normalise a salt subject (a user ID)."""
    if salt_subject is None:
        raise MissingCacheSalt("salt_subject (the user ID) is required for every model call")
    key = str(salt_subject).strip()
    if not key:
        raise MissingCacheSalt("salt_subject must not be empty")
    return key


class CacheSalter:
    def __init__(self, secret: SecretStr) -> None:
        self._key = secret.get_secret_value().encode()

    def salt(self, salt_subject: str | UUID | None) -> str:
        digest = hmac.new(self._key, subject_key(salt_subject).encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
