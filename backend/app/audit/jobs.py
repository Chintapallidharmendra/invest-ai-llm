"""Imported by the worker at start-up (``discover_job_modules``): registers the audit
crons (verification, partition create-ahead, retention)."""

from app.audit import schedules

schedules.register()
