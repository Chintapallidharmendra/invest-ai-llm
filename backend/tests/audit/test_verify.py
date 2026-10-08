"""AC #4: verification, tamper detection, and the scheduled mode."""

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, text

from app.audit import verify, writer
from app.audit.audit_events import MismatchReason, VerifyMode
from app.audit.chain import VERIFY_LOCK_KEY
from app.audit.models import AuditEventRow
from app.audit.settings import AuditSettings
from app.audit.testing import assert_no_content
from app.core import events
from app.core.db import transaction
from app.core.timeutil import utcnow
from tests.audit.conftest import ACTOR, CORRELATION, tamper, tiny
from tests.conftest import PgDatabase

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("audit_db")]


@pytest.fixture
def mismatches() -> Iterator[list[dict[str, Any]]]:
    seen: list[dict[str, Any]] = []

    async def on_mismatch(**payload: Any) -> None:
        seen.append(payload)

    unsubscribe = events.subscribe(verify.CHAIN_MISMATCH_EVENT, on_mismatch)
    yield seen
    unsubscribe()


async def _record_many(n: int) -> None:
    for i in range(n):
        await writer.record(tiny(i), actor_user_id=ACTOR, correlation_id=CORRELATION)


async def _events(event_type: str) -> list[AuditEventRow]:
    async with transaction() as tx:
        stmt = (
            select(AuditEventRow)
            .where(AuditEventRow.event_type == event_type)
            .order_by(AuditEventRow.seq)
        )
        return list((await tx.execute(stmt)).scalars())


async def test_success_records_audit_verified() -> None:
    await _record_many(5)
    result = await verify.run_verification(VerifyMode.FULL)
    assert result is not None
    assert result.ok
    assert (result.from_seq, result.through_seq, result.row_count) == (0, 5, 5)
    (event,) = await _events("audit.verified")
    assert event.payload == {"mode": "full", "from_seq": 0, "through_seq": 5, "row_count": 5}
    assert event.actor_user_id is None
    assert_no_content(event.payload)


async def test_incremental_continues_from_the_last_verified_seq() -> None:
    await _record_many(3)
    await verify.run_verification(VerifyMode.FULL)  # seq 1-3 verified; event is seq 4
    await _record_many(2)  # seq 5-6
    result = await verify.run_verification(VerifyMode.INCREMENTAL)
    assert result is not None
    assert result.ok
    assert (result.from_seq, result.through_seq, result.row_count) == (3, 6, 3)


async def test_incremental_without_history_falls_back_to_full() -> None:
    await _record_many(2)
    result = await verify.run_verification(VerifyMode.INCREMENTAL)
    assert result is not None
    assert result.mode is VerifyMode.FULL


async def test_the_scheduled_mode_is_full_weekly(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AuditSettings(full_verify_interval_days=7)
    await _record_many(1)
    first = await verify.run_verification(settings=settings)
    assert first is not None
    assert first.mode is VerifyMode.FULL  # no full run yet
    second = await verify.run_verification(settings=settings)
    assert second is not None
    assert second.mode is VerifyMode.INCREMENTAL
    later = utcnow() + timedelta(days=7, minutes=1)
    monkeypatch.setattr(verify, "utcnow", lambda: later)
    third = await verify.run_verification(settings=settings)
    assert third is not None
    assert third.mode is VerifyMode.FULL


@pytest.mark.parametrize(
    ("sql", "reason", "bad_seq"),
    [
        ("UPDATE audit_events SET payload = '{\"n\": 99}' WHERE seq = 3", MismatchReason.HASH, 3),
        (
            "UPDATE audit_events SET event_type = 'audit.forged' WHERE seq = 2",
            MismatchReason.HASH,
            2,
        ),
        ("UPDATE audit_events SET prev_hash = hash WHERE seq = 4", MismatchReason.PREV_HASH, 4),
        ("DELETE FROM audit_events WHERE seq = 3", MismatchReason.GAP, 3),
        ("UPDATE audit_events SET seq = seq + 10 WHERE seq = 5", MismatchReason.GAP, 5),
    ],
)
async def test_tampering_is_detected(
    pg: PgDatabase,
    mismatches: list[dict[str, Any]],
    sql: str,
    reason: MismatchReason,
    bad_seq: int,
) -> None:
    await _record_many(5)
    await tamper(pg, sql)
    result = await verify.run_verification(VerifyMode.FULL)
    assert result is not None
    assert not result.ok
    assert (result.reason, result.mismatch_seq) == (reason, bad_seq)
    assert mismatches == [{"seq": bad_seq, "reason": reason}]
    (failed,) = await _events("audit.verify_failed")
    assert failed.payload == {"mode": "full", "seq": bad_seq, "reason": reason.value}
    assert await _events("audit.verified") == []


async def test_a_failure_keeps_failing_until_resolved(
    pg: PgDatabase, mismatches: list[dict[str, Any]]
) -> None:
    await _record_many(3)
    await verify.run_verification(VerifyMode.FULL)
    await _record_many(2)  # seq 5-6
    await tamper(pg, "UPDATE audit_events SET payload = '{\"n\": 7}' WHERE seq = 6")
    for _ in range(2):  # incremental restarts from the last *verified* seq each time
        result = await verify.run_verification(VerifyMode.INCREMENTAL)
        assert result is not None
        assert (result.reason, result.mismatch_seq) == (MismatchReason.HASH, 6)
    assert len(mismatches) == 2


async def test_only_one_verification_runs_at_a_time() -> None:
    await _record_many(1)
    held = asyncio.Event()
    release = asyncio.Event()

    async def hold_lock() -> None:
        async with transaction() as tx:
            await tx.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": VERIFY_LOCK_KEY})
            held.set()
            await release.wait()

    holder = asyncio.create_task(hold_lock())
    await held.wait()
    try:
        assert await verify.run_verification(VerifyMode.FULL) is None
    finally:
        release.set()
        await holder
    assert await verify.run_verification(VerifyMode.FULL) is not None


async def test_an_empty_chain_verifies() -> None:
    result = await verify.run_verification(VerifyMode.FULL)
    assert result is not None
    assert result.ok
    assert result.row_count == 0
