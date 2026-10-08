"""The only way to write audit events (ADR-032)::

    await writer.record(
        DocumentDeleted(document_id=doc.id, reason=DeletionReason.USER, size_bytes=n),
        actor_user_id=user.id,
        correlation_id=correlation_id,
        target_type="document",
        target_id=doc.id,
        session=tx,  # the caller's transaction: the event commits or rolls back with it
    )

Each call takes the transaction-scoped advisory lock, so ``seq`` is gap-free: a
rolled-back transaction releases the lock without consuming a number. The row's
``prev_hash`` is the previous row's ``hash`` (or the latest retention anchor's, or 32
zero bytes for the first event ever).
"""

import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import registry
from app.audit.chain import WRITER_LOCK_KEY, ZERO_HASH, ChainRow, payload_of
from app.audit.check_models import violations
from app.audit.models import AuditAnchor, AuditEventRow
from app.audit.settings import get_audit_settings
from app.audit.types import AuditEvent, AuditPriority
from app.core.db import transaction
from app.core.ids import new_uuid7
from app.core.timeutil import utcnow

_CHECK_VIOLATION: Final = "23514"  # SQLSTATE of "no partition of relation ... found for row"


class InvalidAuditEventError(TypeError):
    """The event's model is unregistered or not metadata-only."""


@dataclass(frozen=True, slots=True)
class RecordedEvent:
    id: uuid.UUID
    seq: int
    hash: bytes


async def record(
    event: AuditEvent,
    *,
    actor_user_id: uuid.UUID | None,
    correlation_id: uuid.UUID | str,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    priority: AuditPriority | str = AuditPriority.NORMAL,
    session: AsyncSession | None = None,
) -> RecordedEvent:
    """Append ``event`` to the chain.

    Pass ``session`` to write inside the caller's transaction (the usual case); without
    it the event is written in a transaction of its own.
    """
    _check_event(event)
    meta = _Meta(
        actor_user_id=actor_user_id,
        correlation_id=uuid.UUID(str(correlation_id)),
        target_type=target_type,
        target_id=target_id,
        priority=AuditPriority(priority),
    )
    if session is not None:
        return await _append(session, event, meta)
    async with transaction() as tx:
        return await _append(tx, event, meta)


@dataclass(frozen=True, slots=True)
class _Meta:
    actor_user_id: uuid.UUID | None
    correlation_id: uuid.UUID
    target_type: str | None
    target_id: uuid.UUID | None
    priority: AuditPriority


def _check_event(event: AuditEvent) -> None:
    model = type(event)
    if registry.get(model.event_type) is not model:
        raise InvalidAuditEventError(f"unregistered audit event: {model.__qualname__}")
    problems = _problems(model)
    if problems:
        raise InvalidAuditEventError(f"{model.__qualname__} is not metadata-only: {problems}")


_checked: dict[type[AuditEvent], list[str]] = {}


def _problems(model: type[AuditEvent]) -> list[str]:
    if model not in _checked:
        _checked[model] = violations(model)
    return _checked[model]


async def chain_head(session: AsyncSession) -> tuple[int, bytes]:
    """``(seq, hash)`` of the newest event, else of the newest anchor, else ``(0, zeros)``."""
    last = (
        await session.execute(
            select(AuditEventRow.seq, AuditEventRow.hash)
            .order_by(AuditEventRow.seq.desc())
            .limit(1)
        )
    ).first()
    if last is not None:
        return last.seq, last.hash
    anchor = (
        await session.execute(
            select(AuditAnchor.last_seq, AuditAnchor.last_hash)
            .order_by(AuditAnchor.last_seq.desc())
            .limit(1)
        )
    ).first()
    if anchor is not None:
        return anchor.last_seq, anchor.last_hash
    return 0, ZERO_HASH


async def _append(session: AsyncSession, event: AuditEvent, meta: _Meta) -> RecordedEvent:
    payload = payload_of(event)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": WRITER_LOCK_KEY})
    last_seq, last_hash = await chain_head(session)
    row = ChainRow(
        id=new_uuid7(),
        seq=last_seq + 1,
        occurred_at=utcnow(),
        correlation_id=meta.correlation_id,
        actor_user_id=meta.actor_user_id,
        event_type=type(event).event_type,
        target_type=meta.target_type,
        target_id=meta.target_id,
        priority=meta.priority.value,
        payload=payload,
        prev_hash=last_hash,
    )
    digest = row.compute_hash()
    values = {
        "id": row.id,
        "seq": row.seq,
        "occurred_at": row.occurred_at,
        "correlation_id": row.correlation_id,
        "actor_user_id": row.actor_user_id,
        "event_type": row.event_type,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "priority": meta.priority,
        "payload": dict(payload),
        "prev_hash": row.prev_hash,
        "hash": digest,
    }
    try:
        async with session.begin_nested():
            await session.execute(insert(AuditEventRow).values(**values))
    except DBAPIError as exc:
        if not _is_missing_partition(exc):
            raise
        # The month's partition is missing (e.g. the create-ahead job hasn't run since a
        # long outage): create it and the next ones, then retry once.
        await session.execute(
            text("SELECT audit_ensure_partitions(:n)"),
            {"n": get_audit_settings().partitions_ahead},
        )
        await session.execute(insert(AuditEventRow).values(**values))
    return RecordedEvent(row.id, row.seq, digest)


def _is_missing_partition(exc: DBAPIError) -> bool:
    cause = getattr(exc.orig, "__cause__", None) or exc.orig
    return getattr(cause, "sqlstate", None) == _CHECK_VIOLATION and "no partition" in str(cause)
