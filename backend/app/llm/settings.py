"""LLM gateway settings (ADR-009, ADR-021, ADR-031).

Environment (``APP_LLM_`` prefix):

- ``APP_LLM_PROFILES_PATH``: profiles file (default: ``profiles.yaml`` next to this module).
- ``APP_LLM_BASE_URLS``: JSON map overriding profile base URLs, e.g.
  ``{"chat": "http://127.0.0.1:8000/v1"}`` (tests point both at the fake LLM).
- ``APP_LLM_PREFIX_CACHING_DISABLED``: acknowledges that prefix caching is off on the
  servers. Required before the gateway starts with a profile that has
  ``supports_cache_salt: false``.

The salt secret comes from the Compose secret ``/run/secrets/cache_salt_secret``, or
from ``APP_LLM_CACHE_SALT_SECRET`` outside containers.
"""

from pathlib import Path
from typing import Final

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings

from app.core.config import settings_config

DEFAULT_PROFILES_PATH: Final = Path(__file__).with_name("profiles.yaml")
MIN_SALT_SECRET_LENGTH: Final = 32


class LLMSettings(BaseSettings):
    model_config = settings_config("APP_LLM_")

    profiles_path: Path = DEFAULT_PROFILES_PATH
    base_urls: dict[str, str] = Field(default_factory=dict)
    cache_salt_secret: SecretStr = Field(
        validation_alias=AliasChoices("APP_LLM_CACHE_SALT_SECRET", "cache_salt_secret")
    )
    prefix_caching_disabled: bool = False

    @field_validator("cache_salt_secret")
    @classmethod
    def _long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().strip()) < MIN_SALT_SECRET_LENGTH:
            raise ValueError(f"cache_salt_secret must be at least {MIN_SALT_SECRET_LENGTH} chars")
        return value
