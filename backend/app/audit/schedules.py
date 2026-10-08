"""Audit crons (ADR-017): daily verification, monthly create-ahead and retention.

:func:`register` is called by ``app/audit/jobs.py``, which the worker imports at start
(``discover_job_modules``). It is idempotent.
"""

from typing import Final

from app.audit import partitions, verify
from app.audit.settings import AuditSettings, get_audit_settings
from app.jobs.scheduler import CronJob, register_cron, unregister_cron

VERIFY_CRON: Final = "audit_verify"
PARTITIONS_CRON: Final = "audit_partitions"
RETENTION_CRON: Final = "audit_retention"


async def run_verify() -> None:
    await verify.run_verification()


async def run_partitions() -> None:
    await partitions.ensure_partitions()


async def run_retention() -> None:
    await partitions.drop_expired_partitions()


def register(settings: AuditSettings | None = None) -> list[CronJob]:
    settings = settings or get_audit_settings()
    crons = [
        (VERIFY_CRON, settings.verify_cron, run_verify),
        (PARTITIONS_CRON, settings.partitions_cron, run_partitions),
        (RETENTION_CRON, settings.retention_cron, run_retention),
    ]
    registered = []
    for name, expr, func in crons:
        unregister_cron(name)
        registered.append(register_cron(name, expr, func))
    return registered
