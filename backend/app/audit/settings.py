"""Audit settings (ADR-032, ADR-035, ADR-021).

Environment (``APP_AUDIT_`` prefix), e.g. ``APP_AUDIT_RETENTION_MONTHS=60``.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Final

from pydantic import Field
from pydantic_settings import BaseSettings

from app.core.config import SECRETS_DIR, settings_config

DEFAULT_HMAC_KEY_PATH: Final = SECRETS_DIR / "audit_hmac_key"


class AuditSettings(BaseSettings):
    model_config = settings_config("APP_AUDIT_")

    # HMAC-SHA256 key for KeyedHash (a Compose secret; at least 32 bytes).
    hmac_key_path: Path = DEFAULT_HMAC_KEY_PATH

    # Monthly partitions older than this are dropped (with a chain anchor). 5 years.
    retention_months: Annotated[int, Field(ge=1, le=240)] = 60
    # Partitions kept ready beyond the current month.
    partitions_ahead: Annotated[int, Field(ge=1, le=12)] = 2

    # Verification: rows read per query, and how often the whole chain is recomputed.
    verify_batch_size: Annotated[int, Field(ge=100, le=100_000)] = 5000
    full_verify_interval_days: Annotated[int, Field(ge=1, le=31)] = 7

    # Crons (5-field crontab, Asia/Kolkata): daily verify, monthly create-ahead and drop.
    verify_cron: str = "30 2 * * *"
    partitions_cron: str = "15 2 1 * *"
    retention_cron: str = "45 3 1 * *"


@lru_cache
def get_audit_settings() -> AuditSettings:
    return AuditSettings()
