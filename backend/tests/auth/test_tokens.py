"""AC #5: opaque tokens and their hashes."""

import base64
import hashlib
import math
from collections import Counter

from app.auth.tokens import TOKEN_BYTES, TOKEN_HASH_BYTES, generate_token, hash_token

URLSAFE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def test_token_is_32_random_bytes_urlsafe() -> None:
    token = generate_token()
    assert len(token) == 43
    assert set(token) <= URLSAFE
    assert len(base64.urlsafe_b64decode(token + "=")) == TOKEN_BYTES


def test_tokens_are_unique_and_high_entropy() -> None:
    tokens = [generate_token() for _ in range(2000)]
    assert len(set(tokens)) == len(tokens)
    raw = b"".join(base64.urlsafe_b64decode(t + "=") for t in tokens)
    counts = Counter(raw)
    entropy = -sum(c / len(raw) * math.log2(c / len(raw)) for c in counts.values())
    assert entropy > 7.9  # bits per byte; uniform random is 8


def test_hash_is_deterministic_sha256_bytes() -> None:
    token = generate_token()
    digest = hash_token(token)
    assert isinstance(digest, bytes)
    assert len(digest) == TOKEN_HASH_BYTES
    assert digest == hash_token(token)
    assert digest == hashlib.sha256(token.encode()).digest()
    assert hash_token(generate_token()) != digest
