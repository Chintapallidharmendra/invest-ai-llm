"""Admin settings.

Environment (``APP_ADMIN_`` prefix), e.g. ``APP_ADMIN_SITE_URL=https://invest-ai.example``.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings

from app.core.config import settings_config


class AdminSettings(BaseSettings):
    model_config = settings_config("APP_ADMIN_")

    # Base of the set-password links handed to new users (the site users open).
    site_url: str = "https://localhost"


@lru_cache
def get_admin_settings() -> AdminSettings:
    return AdminSettings()
