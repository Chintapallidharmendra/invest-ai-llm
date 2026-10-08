"""Crypto settings (ADR-028, ADR-021).

Environment (``APP_CRYPTO_`` prefix), e.g. ``APP_CRYPTO_KEK_VERSION=2``. The KEK itself
is a Compose secret file, never an environment variable.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Final

from pydantic import Field
from pydantic_settings import BaseSettings

from app.core.config import SECRETS_DIR, settings_config

DEFAULT_KEK_PATH: Final = SECRETS_DIR / "kek"
MAX_KEY_CACHE_TTL_S: Final = 300


class CryptoSettings(BaseSettings):
    model_config = settings_config("APP_CRYPTO_")

    # The KEK file: 32 raw bytes, or 64 hex digits (`openssl rand -hex 32`, the runbook),
    # or base64 of 32 bytes. Root-only on the host; never in the DB or backups.
    kek_path: Path = DEFAULT_KEK_PATH
    # The version stored with every wrapped space key; bumped by rotation.
    kek_version: Annotated[int, Field(ge=1)] = 1

    # Unwrapped space and object keys stay in memory at most this long (ADR-028: 5 min).
    key_cache_ttl_s: Annotated[float, Field(gt=0, le=MAX_KEY_CACHE_TTL_S)] = 300
    key_cache_max_entries: Annotated[int, Field(ge=1, le=100_000)] = 4096


@lru_cache
def get_crypto_settings() -> CryptoSettings:
    return CryptoSettings()
