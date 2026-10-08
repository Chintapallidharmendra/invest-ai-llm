"""Audit: partitioned audit_events, audit_anchors, append-only trigger, grants.

Revision ID: 7_1_audit
Revises: 2_1_auth_tables
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7_1_audit"
down_revision: str | Sequence[str] | None = "2_1_auth_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INITIAL_MONTHS_AHEAD = 2

# Rejects UPDATE and DELETE for every role, owner included. (DROP/DETACH of a whole
# partition is retention, done only by app_migrator after writing an anchor.)
_REJECT_FUNCTION = """
CREATE FUNCTION audit_reject_modification() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit tables are append-only: % on % is not allowed', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""

# Creates the monthly partition holding ``month`` (UTC month bounds) if it is missing.
# SECURITY DEFINER: runs as app_migrator, so app_rw (worker, writer) can create partitions
# ahead of time without DDL rights. New partitions get no app_rw grants at all: rows are
# read and written through the parent table only.
_CREATE_PARTITION_FUNCTION = """
CREATE FUNCTION audit_create_partition(month date) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    v_start timestamp := date_trunc('month', month::timestamp);
    v_lower timestamptz := v_start AT TIME ZONE 'UTC';
    v_upper timestamptz := (v_start + interval '1 month') AT TIME ZONE 'UTC';
    v_name text := format('audit_events_y%sm%s', to_char(v_start, 'YYYY'), to_char(v_start, 'MM'));
BEGIN
    IF to_regclass(v_name) IS NOT NULL THEN
        RETURN false;
    END IF;
    EXECUTE format(
        'CREATE TABLE %I PARTITION OF audit_events FOR VALUES FROM (%L) TO (%L)',
        v_name, v_lower, v_upper
    );
    EXECUTE format('REVOKE ALL ON %I FROM app_rw', v_name);
    RETURN true;
END;
$$
"""

_ENSURE_PARTITIONS_FUNCTION = """
CREATE FUNCTION audit_ensure_partitions(months_ahead integer) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    this_month date := date_trunc('month', now() AT TIME ZONE 'UTC')::date;
    created integer := 0;
BEGIN
    IF months_ahead < 0 OR months_ahead > 12 THEN
        RAISE EXCEPTION 'months_ahead must be between 0 and 12';
    END IF;
    FOR i IN 0..months_ahead LOOP
        IF audit_create_partition((this_month + make_interval(months => i))::date) THEN
            created := created + 1;
        END IF;
    END LOOP;
    RETURN created;
END;
$$
"""


def upgrade() -> None:
    audit_priority = postgresql.ENUM("normal", "high", name="audit_priority")
    audit_priority.create(op.get_bind())

    op.create_table(
        "audit_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("correlation_id", sa.UUID(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", sa.UUID(), nullable=True),
        sa.Column(
            "priority",
            postgresql.ENUM(name="audit_priority", create_type=False),
            server_default="normal",
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("prev_hash", sa.LargeBinary(), nullable=False),
        sa.Column("hash", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint("seq > 0", name=op.f("ck_audit_events_seq_positive")),
        sa.CheckConstraint(
            "octet_length(prev_hash) = 32", name=op.f("ck_audit_events_prev_hash_length")
        ),
        sa.CheckConstraint("octet_length(hash) = 32", name=op.f("ck_audit_events_hash_length")),
        sa.CheckConstraint(
            r"event_type ~ '^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$'",
            name=op.f("ck_audit_events_event_type"),
        ),
        sa.CheckConstraint(
            r"target_type ~ '^[a-z][a-z0-9_]{0,31}$'", name=op.f("ck_audit_events_target_type")
        ),
        sa.PrimaryKeyConstraint("id", "occurred_at", name=op.f("pk_audit_events")),
        sa.UniqueConstraint("seq", "occurred_at", name="uq_audit_events_seq"),
        postgresql_partition_by="RANGE (occurred_at)",
    )
    op.create_index(
        "ix_audit_events_actor_user_id_occurred_at",
        "audit_events",
        ["actor_user_id", "occurred_at"],
    )
    op.create_index(
        "ix_audit_events_event_type_occurred_at", "audit_events", ["event_type", "occurred_at"]
    )

    op.create_table(
        "audit_anchors",
        sa.Column("partition_name", sa.Text(), nullable=False),
        sa.Column("last_seq", sa.BigInteger(), nullable=False),
        sa.Column("last_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "dropped_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("last_seq >= 0", name=op.f("ck_audit_anchors_last_seq_non_negative")),
        sa.CheckConstraint(
            "octet_length(last_hash) = 32", name=op.f("ck_audit_anchors_last_hash_length")
        ),
        sa.PrimaryKeyConstraint("partition_name", name=op.f("pk_audit_anchors")),
    )

    op.execute(_REJECT_FUNCTION)
    for table in ("audit_events", "audit_anchors"):
        # A row trigger on the partitioned table is cloned onto every partition.
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION audit_reject_modification()"
        )

    # Default privileges gave app_rw full DML; audit is INSERT/SELECT (anchors: SELECT).
    op.execute("REVOKE ALL ON audit_events, audit_anchors FROM app_rw")
    op.execute("GRANT SELECT, INSERT ON audit_events TO app_rw")
    op.execute("GRANT SELECT ON audit_anchors TO app_rw")

    op.execute(_CREATE_PARTITION_FUNCTION)
    op.execute(_ENSURE_PARTITIONS_FUNCTION)
    for function in ("audit_create_partition(date)", "audit_ensure_partitions(integer)"):
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO app_rw")
    op.execute(f"SELECT audit_ensure_partitions({INITIAL_MONTHS_AHEAD})")


def downgrade() -> None:
    op.execute("DROP FUNCTION audit_ensure_partitions(integer)")
    op.execute("DROP FUNCTION audit_create_partition(date)")
    op.drop_table("audit_anchors")
    op.drop_index("ix_audit_events_event_type_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_user_id_occurred_at", table_name="audit_events")
    op.drop_table("audit_events")  # drops its partitions too
    op.execute("DROP FUNCTION audit_reject_modification()")
    postgresql.ENUM(name="audit_priority").drop(op.get_bind())
