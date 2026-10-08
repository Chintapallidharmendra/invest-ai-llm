"""Monthly partitions of ``audit_events``: create ahead, drop after retention (ADR-035).

- :func:`ensure_partitions` (``app_rw``) calls the ``audit_ensure_partitions`` SQL
  function (SECURITY DEFINER, owned by ``app_migrator``) to create the current month's
  partition and the next ``partitions_ahead``. The writer also calls it if a row finds
  no partition, so a long outage can't block auditing.
- :func:`drop_partition_with_anchor` (``app_migrator``) writes the anchor (the
  partition's last seq and hash) **before** detaching and dropping, all in one
  transaction. Only the oldest partition, and never the current month's, may be dropped,
  so the chain only ever loses a prefix that the newest anchor bridges.
- :func:`drop_expired_partitions` drops partitions wholly older than
  ``retention_months``; until there are any it is a no-op and needs no migrator
  connection.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

from app.audit import writer
from app.audit.audit_events import AuditPartitionDropped
from app.audit.chain import WRITER_LOCK_KEY, ZERO_HASH
from app.audit.settings import AuditSettings, get_audit_settings
from app.core.config import get_settings
from app.core.db import transaction
from app.core.ids import new_uuid7
from app.core.obs import current_correlation_id, get_logger
from app.core.timeutil import utcnow

PARENT: Final = "audit_events"
_NAME: Final = re.compile(r"audit_events_y([0-9]{4})m(0[1-9]|1[0-2])")

_log = get_logger("audit.partitions")


class RetentionError(RuntimeError):
    """A partition drop that would break the chain or lose live data was refused."""


@dataclass(frozen=True, slots=True, order=True)
class Partition:
    month: date
    name: str


def partition_name(month: date) -> str:
    return f"{PARENT}_y{month.year:04d}m{month.month:02d}"


def parse_partition_name(name: str) -> date:
    match = _NAME.fullmatch(name)
    if match is None:
        raise ValueError(f"not an audit partition name: {name!r}")
    return date(int(match[1]), int(match[2]), 1)


def add_months(month: date, months: int) -> date:
    index = month.year * 12 + month.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def month_of(moment: datetime) -> date:
    utc = moment.astimezone(UTC)
    return date(utc.year, utc.month, 1)


async def list_partitions(conn: AsyncSession | AsyncConnection) -> list[Partition]:
    rows = await conn.execute(
        text(
            "SELECT c.relname FROM pg_inherits i "
            "JOIN pg_class c ON c.oid = i.inhrelid "
            "WHERE i.inhparent = 'audit_events'::regclass"
        )
    )
    partitions = []
    for (name,) in rows:
        try:
            partitions.append(Partition(parse_partition_name(name), name))
        except ValueError:
            continue  # not one of ours; never touched
    return sorted(partitions)


async def create_partition(session: AsyncSession, month: date) -> bool:
    """Create the partition for ``month`` if missing (any role with EXECUTE)."""
    return bool(
        (
            await session.execute(text("SELECT audit_create_partition(:m)"), {"m": month})
        ).scalar_one()
    )


async def ensure_partitions(settings: AuditSettings | None = None) -> int:
    """Create the current month's partition and the next ones; returns how many."""
    settings = settings or get_audit_settings()
    async with transaction() as tx:
        created = int(
            (
                await tx.execute(
                    text("SELECT audit_ensure_partitions(:n)"), {"n": settings.partitions_ahead}
                )
            ).scalar_one()
        )
    _log.info("audit.partitions_ensured", module="audit.partitions", created_count=created)
    return created


async def drop_partition_with_anchor(
    name: str, *, migrator_url: str | None = None, now: datetime | None = None
) -> tuple[int, bytes]:
    """Anchor, detach and drop partition ``name`` as ``app_migrator``; returns the anchor."""
    month = parse_partition_name(name)
    if month >= month_of(now or utcnow()):
        raise RetentionError(f"refusing to drop the current or a future partition: {name}")
    url = migrator_url or get_settings().migrator_database_url.get_secret_value()
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            # Serialise with writers and other drops while the anchor is taken.
            await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": WRITER_LOCK_KEY})
            partitions = await list_partitions(conn)
            if not partitions or partitions[0].name != name:
                raise RetentionError(f"only the oldest audit partition may be dropped: {name}")
            # The name is validated above: only [a-z0-9_] reach the SQL text.
            last = (
                await conn.execute(
                    text(f"SELECT seq, hash FROM {name} ORDER BY seq DESC LIMIT 1")  # noqa: S608
                )
            ).first()
            if last is None:  # empty partition: carry the previous anchor forward
                last = (
                    await conn.execute(
                        text(
                            "SELECT last_seq, last_hash FROM audit_anchors "
                            "ORDER BY last_seq DESC LIMIT 1"
                        )
                    )
                ).first()
            last_seq, last_hash = (int(last[0]), bytes(last[1])) if last else (0, ZERO_HASH)
            await conn.execute(
                text(
                    "INSERT INTO audit_anchors (partition_name, last_seq, last_hash) "
                    "VALUES (:name, :seq, :hash)"
                ),
                {"name": name, "seq": last_seq, "hash": last_hash},
            )
            await conn.execute(text(f"ALTER TABLE {PARENT} DETACH PARTITION {name}"))
            await conn.execute(text(f"DROP TABLE {name}"))
    finally:
        await engine.dispose()

    await writer.record(
        AuditPartitionDropped(
            partition_month=datetime(month.year, month.month, 1, tzinfo=UTC), last_seq=last_seq
        ),
        actor_user_id=None,
        correlation_id=current_correlation_id() or str(new_uuid7()),
    )
    _log.info("audit.partition_dropped", module="audit.partitions", dropped_count=1)
    return last_seq, last_hash


def expired(partitions: list[Partition], now: datetime, retention_months: int) -> list[Partition]:
    """Partitions whose whole month ended at least ``retention_months`` ago."""
    cutoff = add_months(month_of(now), -retention_months)
    return [p for p in partitions if add_months(p.month, 1) <= cutoff]


async def drop_expired_partitions(
    *, settings: AuditSettings | None = None, now: datetime | None = None
) -> list[str]:
    """Drop every partition past retention, oldest first. A no-op until one exists."""
    settings = settings or get_audit_settings()
    now = now or utcnow()
    async with transaction() as tx:
        due = expired(await list_partitions(tx), now, settings.retention_months)
    dropped = []
    for partition in due:
        await drop_partition_with_anchor(partition.name, now=now)
        dropped.append(partition.name)
    return dropped
