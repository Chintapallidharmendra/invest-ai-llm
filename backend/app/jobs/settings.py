"""Job queue and worker settings (ADR-017, ADR-029).

Environment (``APP_JOBS_`` prefix), e.g. ``APP_JOBS_HEAVY_CONCURRENCY=2``. The Valkey
URL is also read from ``APP_VALKEY_URL``, the name other modules are expected to share.
"""

from functools import lru_cache
from typing import Annotated

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings

from app.core.config import settings_config

PositiveSeconds = Annotated[float, Field(gt=0)]


class JobsSettings(BaseSettings):
    model_config = settings_config("APP_JOBS_")

    valkey_url: SecretStr = Field(
        default=SecretStr("valkey://valkey:6379/0"),
        validation_alias=AliasChoices("APP_JOBS_VALKEY_URL", "APP_VALKEY_URL", "valkey_url"),
    )

    # Leases: a running job owns its row until lease_until; heartbeats extend it.
    lease_s: PositiveSeconds = 120.0
    heartbeat_s: PositiveSeconds = 30.0
    # How often expired leases are re-queued (also once at start-up).
    reap_interval_s: PositiveSeconds = 30.0
    # Sleep between empty polls.
    poll_interval_s: PositiveSeconds = 1.0

    # Retry delay = base * 2^(attempt-1), capped.
    backoff_base_s: PositiveSeconds = 10.0
    backoff_max_s: PositiveSeconds = 600.0

    # Jobs one worker process runs at once (light and heavy together).
    concurrency: Annotated[int, Field(ge=1)] = 4
    # LLM-heavy jobs running at once across all workers (Valkey semaphore, ADR-029).
    heavy_concurrency: Annotated[int, Field(ge=1)] = 2
    # A crashed holder's slot frees itself after this long without renewal.
    semaphore_ttl_s: PositiveSeconds = 90.0

    # On SIGTERM, in-flight jobs get this long to finish before their leases are released.
    shutdown_grace_s: Annotated[float, Field(ge=0)] = 20.0

    # Cron timezone (APScheduler).
    timezone: str = "Asia/Kolkata"

    # Worker liveness/readiness endpoint; never published outside the container.
    health_host: str = "127.0.0.1"
    health_port: Annotated[int, Field(ge=0, le=65535)] = 8081


@lru_cache
def get_jobs_settings() -> JobsSettings:
    return JobsSettings()
