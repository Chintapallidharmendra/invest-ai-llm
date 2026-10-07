"""Base settings (ADR-021).

Only this module reads the environment. Each feature module declares its own settings
class in ``app/<module>/settings.py`` using :func:`settings_config`.
"""

from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SECRETS_DIR: Final = Path("/run/secrets")

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


def settings_config(env_prefix: str = "APP_") -> SettingsConfigDict:
    """Shared pydantic-settings config: ``APP_`` env prefix plus Compose secrets."""
    return SettingsConfigDict(
        env_prefix=env_prefix,
        # Compose mounts secrets here; outside containers the directory is absent.
        secrets_dir=SECRETS_DIR if SECRETS_DIR.is_dir() else None,
        extra="ignore",
    )


class BaseAppSettings(BaseSettings):
    model_config = settings_config()

    database_url: SecretStr = SecretStr("postgresql+asyncpg://app_rw@localhost:5432/invest_ai")
    migrator_database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://app_migrator@localhost:5432/invest_ai"
    )
    log_level: LogLevel = "INFO"


@lru_cache
def get_settings() -> BaseAppSettings:
    return BaseAppSettings()
