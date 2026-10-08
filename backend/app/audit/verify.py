"""Chain verification (ADR-016/032): recompute hashes and links, record the outcome.

- **Incremental** (daily): rows after the ``through_seq`` of the latest
  ``audit.verified`` event, starting from that row's stored hash.
- **Full** (when the last full run is older than ``full_verify_interval_days``, or there
  is none): the whole chain, starting from the latest retention anchor (or seq 0 and 32
  zero bytes), so a dropped-partition gap is bridged by its anchor.

A mismatch emits the core event ``audit.chain_mismatch`` (admin alerts subscribe) and
records ``audit.verify_failed``; success records ``audit.verified``. Runs as ``app_rw``
(SELECT only); a try-lock keeps two verifications from running at once.
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer
from app.audit.audit_events import AuditVerified, AuditVerifyFailed, MismatchReason, VerifyMode
from app.audit.chain import VERIFY_LOCK_KEY, ZERO_HASH, ChainRow
from app.audit.models import AuditAnchor, AuditEventRow
from app.audit.settings import AuditSettings, get_audit_settings
from app.core import events
from app.core.db import transaction
from app.core.ids import new_uuid7
from app.core.obs import current_correlation_id, get_logger
from app.core.timeutil import utcnow

CHAIN_MISMATCH_EVENT = "audit.chain_mismatch"

_log = get_logger("audit.verify")


@dataclass(frozen=True, slots=True)
class VerifyResult:
    mode: VerifyMode
    from_seq: int
    through_seq: int
    row_count: int
    mismatch_seq: int | None = None
    reason: MismatchReason | None = None

    @property
    def ok(self) -> bool:
        return self.reason is None


async def run_verification(
    mode: VerifyMode | None = None, *, settings: AuditSettings | None = None
) -> VerifyResult | None:
    """Verify, then record the outcome. ``None`` if another verification holds the lock."""
    settings = settings or get_audit_settings()
    async with transaction() as tx:
        locked = (
            await tx.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": VERIFY_LOCK_KEY})
        ).scalar_one()
        if not locked:
            return None
        result = await verify_chain(tx, mode or await _scheduled_mode(tx, settings), settings)

    correlation_id = current_correlation_id() or str(new_uuid7())
    if result.ok:
        await writer.record(
            AuditVerified(
                mode=result.mode,
                from_seq=result.from_seq,
                through_seq=result.through_seq,
                row_count=result.row_count,
            ),
            actor_user_id=None,
            correlation_id=correlation_id,
        )
        _log.info("audit.verified", module="audit.verify", row_count=result.row_count)
        return result

    seq, reason = result.mismatch_seq or 0, result.reason or MismatchReason.GAP
    _log.error("audit.chain_mismatch", module="audit.verify", error_code=reason.value)
    await events.emit(CHAIN_MISMATCH_EVENT, seq=seq, reason=reason)
    await writer.record(
        AuditVerifyFailed(mode=result.mode, seq=seq, reason=reason),
        actor_user_id=None,
        correlation_id=correlation_id,
    )
    return result


async def _latest_verified(
    session: AsyncSession, mode: VerifyMode | None = None
) -> tuple[AuditEventRow, int] | None:
    stmt = select(AuditEventRow).where(AuditEventRow.event_type == AuditVerified.event_type)
    if mode is not None:
        stmt = stmt.where(AuditEventRow.payload["mode"].astext == mode.value)
    row = (await session.execute(stmt.order_by(AuditEventRow.seq.desc()).limit(1))).scalar()
    if row is None:
        return None
    return row, int(row.payload["through_seq"])


async def _scheduled_mode(session: AsyncSession, settings: AuditSettings) -> VerifyMode:
    last_full = await _latest_verified(session, VerifyMode.FULL)
    interval = timedelta(days=settings.full_verify_interval_days)
    if last_full is None or utcnow() - last_full[0].occurred_at >= interval:
        return VerifyMode.FULL
    return VerifyMode.INCREMENTAL


async def _start(session: AsyncSession, mode: VerifyMode) -> tuple[VerifyMode, int, bytes]:
    if mode is VerifyMode.INCREMENTAL:
        latest = await _latest_verified(session)
        if latest is not None:
            through = latest[1]
            stored = (
                await session.execute(
                    select(AuditEventRow.hash).where(AuditEventRow.seq == through)
                )
            ).scalar()
            if stored is not None:
                return mode, through, stored
        mode = VerifyMode.FULL  # nothing to continue from
    anchor = (
        await session.execute(
            select(AuditAnchor.last_seq, AuditAnchor.last_hash)
            .order_by(AuditAnchor.last_seq.desc())
            .limit(1)
        )
    ).first()
    if anchor is not None:
        return mode, anchor.last_seq, anchor.last_hash
    return mode, 0, ZERO_HASH


async def verify_chain(
    session: AsyncSession, mode: VerifyMode, settings: AuditSettings | None = None
) -> VerifyResult:
    """Check every row after the start point up to the current head (no recording)."""
    settings = settings or get_audit_settings()
    mode, start_seq, prev_hash = await _start(session, mode)
    head = (
        await session.execute(select(AuditEventRow.seq).order_by(AuditEventRow.seq.desc()).limit(1))
    ).scalar()
    through = head if head is not None else start_seq
    expected = start_seq + 1
    count = 0

    def failed(seq: int, reason: MismatchReason) -> VerifyResult:
        return VerifyResult(mode, start_seq, through, count, seq, reason)

    table = AuditEventRow.__table__
    columns = table.c
    while expected <= through:
        rows = (
            await session.execute(
                select(table)
                .where(columns.seq >= expected, columns.seq <= through)
                .order_by(columns.seq)
                .limit(settings.verify_batch_size)
            )
        ).all()
        if not rows:
            return failed(expected, MismatchReason.GAP)
        for r in rows:
            if r.seq != expected:
                return failed(expected, MismatchReason.GAP)
            if r.prev_hash != prev_hash:
                return failed(r.seq, MismatchReason.PREV_HASH)
            row = ChainRow(
                id=r.id,
                seq=r.seq,
                occurred_at=r.occurred_at,
                correlation_id=r.correlation_id,
                actor_user_id=r.actor_user_id,
                event_type=r.event_type,
                target_type=r.target_type,
                target_id=r.target_id,
                priority=str(getattr(r.priority, "value", r.priority)),
                payload=r.payload,
                prev_hash=r.prev_hash,
            )
            if row.compute_hash() != r.hash:
                return failed(r.seq, MismatchReason.HASH)
            prev_hash = r.hash
            expected += 1
            count += 1
    return VerifyResult(mode, start_seq, through, count)
