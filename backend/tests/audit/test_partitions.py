"""AC #5: create-ahead, drop with anchor (anchor first), verification across the gap,
retention; AC #4's cron registration."""

import importlib
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine

from app.audit import partitions, schedules, verify, writer
from app.audit.audit_events import VerifyMode
from app.audit.models import AuditAnchor, AuditEventRow
from app.audit.partitions import (
    Partition,
    RetentionError,
    add_months,
    create_partition,
    drop_expired_partitions,
    drop_partition_with_anchor,
    expired,
    list_partitions,
    month_of,
    partition_name,
)
from app.audit.settings import AuditSettings
from app.core.db import transaction
from app.jobs import scheduler
from tests.audit.conftest import ACTOR, CORRELATION, as_migrator, tiny
from tests.conftest import PgDatabase

JAN, FEB = date(2001, 1, 1), date(2001, 2, 1)


# --- Pure helpers -------------------------------------------------------------------------


def test_partition_names_and_month_arithmetic() -> None:
    assert partition_name(date(2026, 3, 1)) == "audit_events_y2026m03"
    assert partitions.parse_partition_name("audit_events_y2026m03") == date(2026, 3, 1)
    with pytest.raises(ValueError, match="not an audit partition"):
        partitions.parse_partition_name("audit_events_y2026m13; DROP TABLE users")
    assert add_months(date(2026, 11, 1), 3) == date(2027, 2, 1)
    assert add_months(date(2026, 1, 1), -60) == date(2021, 1, 1)


def test_expired_partitions_are_whole_months_past_retention() -> None:
    now = datetime(2026, 10, 8, tzinfo=UTC)
    parts = [Partition(date(2021, m, 1), partition_name(date(2021, m, 1))) for m in (8, 9, 10)]
    # 60 months before Oct 2026 is Oct 2021: Aug and Sep 2021 have fully expired.
    assert [p.month.month for p in expired(parts, now, 60)] == [8, 9]
    assert expired(parts, now, 120) == []


# --- Database ----------------------------------------------------------------------------


@pytest.fixture
def statements() -> Iterator[list[str]]:
    """Every SQL statement any engine executes during the test."""
    seen: list[str] = []

    def listener(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", listener)
    yield seen
    event.remove(Engine, "before_cursor_execute", listener)


async def _record_at(monkeypatch: pytest.MonkeyPatch, moment: datetime, n: int) -> None:
    monkeypatch.setattr(writer, "utcnow", lambda: moment)
    await writer.record(tiny(n), actor_user_id=ACTOR, correlation_id=CORRELATION)
    monkeypatch.undo()


async def _old_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """seq 1-3 in Jan 2001, 4-5 in Feb 2001, 6-7 now."""
    async with transaction() as tx:
        assert await create_partition(tx, JAN)
        assert await create_partition(tx, FEB)
        assert not await create_partition(tx, JAN)  # idempotent
    for day, n in ((5, 1), (6, 2), (7, 3)):
        await _record_at(monkeypatch, datetime(2001, 1, day, tzinfo=UTC), n)
    for day, n in ((5, 4), (6, 5)):
        await _record_at(monkeypatch, datetime(2001, 2, day, tzinfo=UTC), n)
    for n in (6, 7):
        await writer.record(tiny(n), actor_user_id=ACTOR, correlation_id=CORRELATION)


@pytest.mark.db
@pytest.mark.usefixtures("audit_db")
async def test_drop_writes_the_anchor_first_and_verification_bridges_the_gap(
    pg: PgDatabase, monkeypatch: pytest.MonkeyPatch, statements: list[str]
) -> None:
    await _old_chain(monkeypatch)
    async with transaction() as tx:
        seq3 = (await tx.execute(select(AuditEventRow).where(AuditEventRow.seq == 3))).scalar_one()

    statements.clear()
    anchor = await drop_partition_with_anchor(partition_name(JAN), migrator_url=pg.migrator_url)
    assert anchor == (3, seq3.hash)

    order = [
        next(i for i, s in enumerate(statements) if marker in s)
        for marker in ("INSERT INTO audit_anchors", "DETACH PARTITION", "DROP TABLE")
    ]
    assert order == sorted(order)

    async with transaction() as tx:
        names = [p.name for p in await list_partitions(tx)]
        anchors = list((await tx.execute(select(AuditAnchor))).scalars())
        seqs = list(
            (await tx.execute(select(AuditEventRow.seq).order_by(AuditEventRow.seq))).scalars()
        )
    assert partition_name(JAN) not in names
    assert [(a.partition_name, a.last_seq, a.last_hash) for a in anchors] == [
        (partition_name(JAN), 3, seq3.hash)
    ]
    assert seqs[:2] == [4, 5]  # seq 1-3 are gone; 8 is audit.partition_dropped

    result = await verify.run_verification(VerifyMode.FULL)
    assert result is not None
    assert result.ok
    assert result.from_seq == 3


@pytest.mark.db
@pytest.mark.usefixtures("audit_db")
async def test_without_the_anchor_the_gap_is_a_mismatch(
    pg: PgDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _old_chain(monkeypatch)
    await as_migrator(pg, f"DROP TABLE {partition_name(JAN)}")  # bypasses the anchor
    result = await verify.run_verification(VerifyMode.FULL)
    assert result is not None
    assert (result.ok, result.mismatch_seq) == (False, 1)


@pytest.mark.db
@pytest.mark.usefixtures("audit_db")
async def test_only_the_oldest_past_partition_may_be_dropped(
    pg: PgDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _old_chain(monkeypatch)
    with pytest.raises(RetentionError, match="oldest"):
        await drop_partition_with_anchor(partition_name(FEB), migrator_url=pg.migrator_url)
    current = partition_name(month_of(datetime.now(UTC)))
    with pytest.raises(RetentionError, match="current or a future"):
        await drop_partition_with_anchor(current, migrator_url=pg.migrator_url)
    async with transaction() as tx:
        assert len(list((await tx.execute(select(AuditAnchor))).scalars())) == 0


@pytest.mark.db
@pytest.mark.usefixtures("audit_db")
async def test_retention_drops_expired_partitions_oldest_first(
    pg: PgDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _old_chain(monkeypatch)
    monkeypatch.setenv("APP_MIGRATOR_DATABASE_URL", pg.migrator_url)
    default = await drop_expired_partitions(settings=AuditSettings())  # 60 months
    assert default == [partition_name(JAN), partition_name(FEB)]
    async with transaction() as tx:
        anchors = list(
            (await tx.execute(select(AuditAnchor).order_by(AuditAnchor.last_seq))).scalars()
        )
    assert [(a.partition_name, a.last_seq) for a in anchors] == [
        (partition_name(JAN), 3),
        (partition_name(FEB), 5),
    ]
    result = await verify.run_verification(VerifyMode.FULL)
    assert result is not None
    assert result.ok


@pytest.mark.db
@pytest.mark.usefixtures("audit_db")
async def test_retention_is_a_no_op_until_data_is_old_enough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_migrator(*args: object, **kwargs: object) -> None:
        raise AssertionError("the no-op must not connect as app_migrator")

    monkeypatch.setattr(partitions, "create_async_engine", no_migrator)
    await writer.record(tiny(), actor_user_id=ACTOR, correlation_id=CORRELATION)
    assert await drop_expired_partitions() == []


@pytest.mark.db
@pytest.mark.usefixtures("audit_db")
async def test_ensure_partitions_is_idempotent(pg: PgDatabase) -> None:
    month = month_of(datetime.now(UTC))
    ahead = partition_name(add_months(month, 2))
    await as_migrator(pg, f"DROP TABLE {ahead}")
    assert await partitions.ensure_partitions() == 1
    assert await partitions.ensure_partitions() == 0
    rows = await as_migrator(pg, "SELECT to_regclass(:n)::text", {"n": ahead})
    assert rows == [(ahead,)]


# --- Crons --------------------------------------------------------------------------------


@pytest.fixture
def clean_crons() -> Iterator[None]:
    for name in (schedules.VERIFY_CRON, schedules.PARTITIONS_CRON, schedules.RETENTION_CRON):
        scheduler.unregister_cron(name)
    yield
    for name in (schedules.VERIFY_CRON, schedules.PARTITIONS_CRON, schedules.RETENTION_CRON):
        scheduler.unregister_cron(name)


@pytest.mark.usefixtures("clean_crons")
async def test_crons_are_registered_with_the_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    schedules.register()
    schedules.register()  # idempotent
    crons = scheduler.registered_crons()
    assert crons["audit_verify"].cron_expr == "30 2 * * *"
    assert crons["audit_partitions"].cron_expr == "15 2 1 * *"
    assert crons["audit_retention"].cron_expr == "45 3 1 * *"

    calls: list[str] = []

    async def fake(name: str, *args: object, **kwargs: object) -> None:
        calls.append(name)

    monkeypatch.setattr(verify, "run_verification", lambda: fake("verify"))
    monkeypatch.setattr(partitions, "ensure_partitions", lambda: fake("partitions"))
    monkeypatch.setattr(partitions, "drop_expired_partitions", lambda: fake("retention"))
    for name in ("audit_verify", "audit_partitions", "audit_retention"):
        await crons[name].func()
    assert calls == ["verify", "partitions", "retention"]


@pytest.mark.usefixtures("clean_crons")
def test_the_worker_hook_registers_the_crons() -> None:
    importlib.reload(importlib.import_module("app.audit.jobs"))
    assert {"audit_verify", "audit_partitions", "audit_retention"} <= set(
        scheduler.registered_crons()
    )
