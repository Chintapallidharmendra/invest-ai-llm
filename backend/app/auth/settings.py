"""Authentication settings (ADR-006, ADR-021).

Environment (``APP_AUTH_`` prefix), e.g. ``APP_AUTH_BREACHED_PASSWORDS_PATH``.
"""

from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Final

from pydantic import Field
from pydantic_settings import BaseSettings

from app.core.config import settings_config

DEFAULT_BREACHED_PASSWORDS_PATH: Final = (
    Path(__file__).with_name("data") / "breached-passwords-top100k.txt"
)


class AuthSettings(BaseSettings):
    model_config = settings_config("APP_AUTH_")

    # Password policy (NIST SP 800-63B style: length + breached list, no composition rules).
    password_min_length: Annotated[int, Field(ge=8)] = 12
    # Upper bound so hashing can't be used for resource exhaustion.
    password_max_length: Annotated[int, Field(ge=64)] = 1024
    breached_passwords_path: Path = DEFAULT_BREACHED_PASSWORDS_PATH

    # Sessions: absolute lifetime, idle timeout, and how often last_seen_at is written.
    session_absolute_ttl_s: Annotated[int, Field(gt=0)] = 12 * 3600
    session_idle_timeout_s: Annotated[int, Field(gt=0)] = 60 * 60
    session_touch_interval_s: Annotated[int, Field(gt=0)] = 60
    # One-time set-password links (invite / reset).
    password_token_ttl_s: Annotated[int, Field(gt=0)] = 24 * 3600

    # Login protection (Story 2.2): consecutive failures before a lock, and its length.
    login_max_failures: Annotated[int, Field(ge=1)] = 5
    lockout_s: Annotated[int, Field(gt=0)] = 15 * 60
    # Login attempts per client IP per window (Valkey fixed window).
    login_rate_limit: Annotated[int, Field(ge=1)] = 10
    login_rate_window_s: Annotated[int, Field(gt=0)] = 60
    # Origins allowed on state-changing requests, e.g. ["https://invest-ai.example"]
    # (JSON list in APP_AUTH_SITE_ORIGINS). A request passes the origin check if its
    # Origin is listed or the browser sends Sec-Fetch-Site: same-origin.
    site_origins: list[str] = []

    @property
    def session_absolute_ttl(self) -> timedelta:
        return timedelta(seconds=self.session_absolute_ttl_s)

    @property
    def session_idle_timeout(self) -> timedelta:
        return timedelta(seconds=self.session_idle_timeout_s)

    @property
    def lockout(self) -> timedelta:
        return timedelta(seconds=self.lockout_s)

    @property
    def password_token_ttl(self) -> timedelta:
        return timedelta(seconds=self.password_token_ttl_s)


@lru_cache
def get_auth_settings() -> AuthSettings:
    return AuthSettings()
