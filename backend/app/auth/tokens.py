"""Opaque secrets for sessions and set-password links (ADR-006).

A raw token goes to the client exactly once (cookie or link fragment). Only
``hash_token(token)`` (SHA-256) is stored, so a database leak yields no usable tokens.
Never persist or log a raw token.
"""

import hashlib
import secrets
from typing import Final

TOKEN_BYTES: Final = 32  # 256 bits
TOKEN_HASH_BYTES: Final = 32  # SHA-256 digest size


def generate_token() -> str:
    """A fresh 256-bit random token, URL-safe base64 without padding (43 chars)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> bytes:
    """SHA-256 of the token's UTF-8 bytes: the value stored and looked up.

    A plain hash is enough: tokens are 256-bit random, so there is nothing to brute-force.
    """
    return hashlib.sha256(token.encode("utf-8")).digest()
