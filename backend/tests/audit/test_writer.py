"""AC #1 (schema, immutability, grants, partitions) and AC #3 (the writer)."""

import asyncio
import itertools
import uuid
from datetime import UTC, datetime
from typing import ClassVar

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.audit import registry, writer
from app.audit.audit_events import VerifyMode
from app.audit.chain import ZERO_HASH
from app.audit.models import AuditEventRow
from app.audit.partitions import add_months, list_partitions, month_of, partition_name
from app.audit.testing import assert_no_content
from app.audit.types import AuditEvent, AuditPriority
from app.audit.verify import verify_chain
from app.core.db import transaction
from tests.audit.conftest import ACTOR, CORRELATION, as_migrator, dummy_event, tiny
from tests.conftest import PgDatabase

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("audit_db")]


async def _rows() -> list[AuditEventRow]:
    async with transaction() as tx:
        return list((await tx.execute(select(AuditEventRow).order_by(AuditEventRow.seq))).scalars())


async def _record(n: int = 1, **kwargs: object) -> writer.RecordedEvent:
    return await writer.record(
        tiny(n),
        actor_user_id=ACTOR,
        correlation_id=CORRELATION,
        **kwargs,  # type: ignore[arg-type]
    )


# --- AC #1: schema, grants, trigger, partitions ------------------------------------------


async def test_app_rw_can_insert_and_select_but_not_update_or_delete() -> None:
    await _record()
    async with transaction() as tx:
        assert (await tx.execute(text("SELECT count(*) FROM audit_events"))).scalar_one() == 1
    for sql in ("UPDATE audit_events SET seq = 99", "DELETE FROM audit_events"):
        with pytest.raises(ProgrammingError, match="permission denied"):
            async with transaction() as tx:
                await tx.execute(text(sql))
    # Partitions carry no app_rw grants: rows are only reachable through the parent.
    month = month_of(datetime.now(UTC))
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with transaction() as tx:
            await tx.execute(text(f"SELECT count(*) FROM {partition_name(month)}"))  # noqa: S608


async def test_anchors_are_read_only_for_app_rw() -> None:
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with transaction() as tx:
            await tx.execute(
                text("INSERT INTO audit_anchors VALUES ('x', 0, :h, now())"), {"h": ZERO_HASH}
            )
    async with transaction() as tx:
        await tx.execute(text("SELECT * FROM audit_anchors"))


async def test_the_trigger_rejects_update_and_delete_even_for_the_owner(pg: PgDatabase) -> None:
    await _record()
    await as_migrator(
        pg,
        "INSERT INTO audit_anchors VALUES ('audit_events_y2000m01', 0, :h, now())",
        {"h": ZERO_HASH},
    )
    for sql in (
        "UPDATE audit_events SET payload = '{}'",
        "DELETE FROM audit_events",
        "UPDATE audit_anchors SET last_seq = 1",
        "DELETE FROM audit_anchors",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            await as_migrator(pg, sql)
    assert len(await _rows()) == 1


async def test_partitions_exist_for_this_month_and_the_next_two(pg: PgDatabase) -> None:
    month = month_of(datetime.now(UTC))
    async with transaction() as tx:
        names = {p.name for p in await list_partitions(tx)}
    assert {partition_name(add_months(month, i)) for i in range(3)} <= names


async def test_a_row_lands_in_its_month_partition(pg: PgDatabase) -> None:
    await _record()
    rows = await as_migrator(pg, "SELECT tableoid::regclass::text FROM audit_events")
    assert rows == [(partition_name(month_of(datetime.now(UTC))),)]


async def test_indexes_exist(pg: PgDatabase) -> None:
    rows = await as_migrator(
        pg, "SELECT indexname FROM pg_indexes WHERE tablename = 'audit_events' ORDER BY 1"
    )
    assert {r[0] for r in rows} >= {
        "ix_audit_events_actor_user_id_occurred_at",
        "ix_audit_events_event_type_occurred_at",
        "uq_audit_events_seq",
        "pk_audit_events",
    }


# --- AC #3: the writer -----------------------------------------------------------------------


async def test_first_event_links_to_zero_hash_and_stores_metadata() -> None:
    target = uuid.uuid4()
    recorded = await writer.record(
        dummy_event(),
        actor_user_id=ACTOR,
        correlation_id=str(CORRELATION),
        target_type="document",
        target_id=target,
        priority="high",
    )
    (row,) = await _rows()
    assert (row.seq, row.prev_hash, row.hash) == (1, ZERO_HASH, recorded.hash)
    assert (row.actor_user_id, row.correlation_id, row.target_id) == (ACTOR, CORRELATION, target)
    assert (row.event_type, row.target_type, row.priority) == (
        "testaudit.dummy",
        "document",
        AuditPriority.HIGH,
    )
    assert row.payload["colour"] == "red"
    assert_no_content(row.payload)


async def test_100_concurrent_records_are_gap_free_and_chained() -> None:
    await asyncio.gather(*(_record(i) for i in range(100)))
    rows = await _rows()
    assert [r.seq for r in rows] == list(range(1, 101))
    for previous, current in itertools.pairwise(rows):
        assert current.prev_hash == previous.hash
    async with transaction() as tx:
        result = await verify_chain(tx, VerifyMode.FULL)
    assert result.ok
    assert result.row_count == 100


async def test_a_rolled_back_event_consumes_no_seq() -> None:
    await _record(1)

    async def record_then_fail() -> None:
        async with transaction() as tx:
            await _record(2, session=tx)
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await record_then_fail()
    await _record(3)
    rows = await _rows()
    assert [(r.seq, r.payload["n"]) for r in rows] == [(1, 1), (2, 3)]


async def test_records_commit_with_the_callers_transaction() -> None:
    async with transaction() as tx:
        await _record(1, session=tx)
        await _record(2, session=tx)
        assert (await tx.execute(text("SELECT count(*) FROM audit_events"))).scalar_one() == 2
    assert [r.seq for r in await _rows()] == [1, 2]


async def test_a_missing_current_partition_is_created_on_write(pg: PgDatabase) -> None:
    name = partition_name(month_of(datetime.now(UTC)))
    await as_migrator(pg, f"DROP TABLE {name}")
    await _record()
    rows = await as_migrator(pg, "SELECT tableoid::regclass::text FROM audit_events")
    assert rows == [(name,)]


async def test_unregistered_or_free_text_events_are_refused() -> None:
    class NoteAdded(AuditEvent):
        event_type: ClassVar[str] = "testaudit.note_added_w"
        note: str

    try:
        with pytest.raises(writer.InvalidAuditEventError, match="not metadata-only"):
            await writer.record(NoteAdded(note="x"), actor_user_id=None, correlation_id=CORRELATION)
    finally:
        registry.unregister("testaudit.note_added_w")
    with pytest.raises(writer.InvalidAuditEventError, match="unregistered"):
        await writer.record(NoteAdded(note="x"), actor_user_id=None, correlation_id=CORRELATION)
    assert await _rows() == []


async def test_bad_correlation_ids_and_target_types_are_refused() -> None:
    with pytest.raises(ValueError, match="badly formed"):
        await writer.record(tiny(), actor_user_id=None, correlation_id="not-a-uuid")
    with pytest.raises(DBAPIError, match="target_type"):
        await writer.record(
            tiny(), actor_user_id=None, correlation_id=CORRELATION, target_type="Deal Falcon"
        )
