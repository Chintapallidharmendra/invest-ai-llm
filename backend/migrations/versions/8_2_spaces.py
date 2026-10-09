"""Spaces, memberships and the RLS foundation (ADR-023).

Creates ``spaces`` and ``space_members``, the SQL functions ``app_user_id()`` and
``app_space_ids()`` that every space policy uses, and the foreign keys from the 8.1 key
tables to ``spaces``.

Revision ID: 8_2_spaces
Revises: 8_1_keys
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8_2_spaces"
down_revision: str | Sequence[str] | None = "8_1_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUMS = {
    "space_kind": ("private", "workspace"),
    "space_role": ("owner", "member"),
}

# NULL when app.user_id is unset or not a UUID: policies then admit nothing, and a
# malformed value never raises (no error leaks through a query).
APP_USER_ID = r"""
CREATE FUNCTION app_user_id() RETURNS uuid
LANGUAGE sql STABLE PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN s ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
        THEN s::uuid
    END
    FROM (SELECT current_setting('app.user_id', true) AS s) AS setting
$$
"""

# SECURITY DEFINER (owned by app_migrator, the owner of spaces and space_members, which
# don't FORCE RLS) so the policies of those two tables don't recurse through this
# function. search_path is pinned so callers can't substitute objects.
APP_SPACE_IDS = """
CREATE FUNCTION app_space_ids() RETURNS uuid[]
LANGUAGE sql STABLE SECURITY DEFINER PARALLEL SAFE
SET search_path = pg_catalog, public
AS $$
    SELECT coalesce(array_agg(m.space_id), '{}'::uuid[])
    FROM public.space_members AS m
    JOIN public.spaces AS s ON s.id = m.space_id
    WHERE m.user_id = public.app_user_id() AND s.closed_at IS NULL
$$
"""

MEMBER_OF = "id = ANY ((SELECT app_space_ids())::uuid[])"
MEMBER_OF_SPACE = "space_id = ANY ((SELECT app_space_ids())::uuid[])"


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def _ts(name: str, *, nullable: bool = False, default: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()") if default else None,
        nullable=nullable,
    )


def upgrade() -> None:
    bind = op.get_bind()
    for name in ENUMS:
        _enum(name).create(bind, checkfirst=False)

    op.create_table(
        "spaces",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("kind", _enum("space_kind"), nullable=False),
        sa.Column("owner_user_id", sa.UUID(), nullable=False),
        sa.Column("code_name_enc", sa.LargeBinary(), nullable=True),
        sa.Column("retention_days", sa.Integer(), server_default=sa.text("30"), nullable=False),
        _ts("retention_extended_at", nullable=True),
        _ts("closed_at", nullable=True),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.CheckConstraint(
            "kind = 'workspace' OR code_name_enc IS NULL",
            name=op.f("ck_spaces_no_code_name_when_private"),
        ),
        sa.CheckConstraint(
            "retention_days BETWEEN 1 AND 3650", name=op.f("ck_spaces_retention_days_range")
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_spaces_owner_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spaces")),
    )
    op.create_index("ix_spaces_owner_user_id", "spaces", ["owner_user_id"])
    op.create_index(
        "uq_spaces_private_owner",
        "spaces",
        ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'private'"),
    )

    op.create_table(
        "space_members",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", _enum("space_role"), nullable=False),
        sa.Column("added_by", sa.UUID(), nullable=True),
        _ts("added_at", default=True),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["spaces.id"],
            name=op.f("fk_space_members_space_id_spaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_space_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["added_by"],
            ["users.id"],
            name=op.f("fk_space_members_added_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("space_id", "user_id", name=op.f("pk_space_members")),
    )
    op.create_index("ix_space_members_user_id", "space_members", ["user_id"])

    # The key tables' space_id now references spaces (deferred by 8.1).
    for table in ("space_keys", "object_keys"):
        op.create_foreign_key(
            op.f(f"fk_{table}_space_id_spaces"), table, "spaces", ["space_id"], ["id"]
        )

    # RLS functions, executable by the app only.
    op.execute(APP_USER_ID)
    op.execute(APP_SPACE_IDS)
    for function in ("app_user_id()", "app_space_ids()"):
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO app_rw")

    # Member-only policies. Inserts are open to the app (creating a space or adding a
    # member happens before the new member's context can see it); who may do so is
    # decided in app.spaces. Reads, updates and deletes need membership.
    op.execute("ALTER TABLE spaces ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY spaces_member_select ON spaces FOR SELECT USING ({MEMBER_OF})")
    op.execute("CREATE POLICY spaces_insert ON spaces FOR INSERT WITH CHECK (true)")
    op.execute(
        f"CREATE POLICY spaces_member_update ON spaces FOR UPDATE "
        f"USING ({MEMBER_OF}) WITH CHECK ({MEMBER_OF})"
    )
    op.execute(f"CREATE POLICY spaces_member_delete ON spaces FOR DELETE USING ({MEMBER_OF})")

    op.execute("ALTER TABLE space_members ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY space_members_member_select ON space_members FOR SELECT "
        f"USING ({MEMBER_OF_SPACE})"
    )
    op.execute("CREATE POLICY space_members_insert ON space_members FOR INSERT WITH CHECK (true)")
    op.execute(
        f"CREATE POLICY space_members_member_update ON space_members FOR UPDATE "
        f"USING ({MEMBER_OF_SPACE}) WITH CHECK ({MEMBER_OF_SPACE})"
    )
    op.execute(
        f"CREATE POLICY space_members_member_delete ON space_members FOR DELETE "
        f"USING ({MEMBER_OF_SPACE})"
    )
    # Default privileges already give app_rw SELECT/INSERT/UPDATE/DELETE: that's intended.


def downgrade() -> None:
    for table in ("space_keys", "object_keys"):
        op.drop_constraint(op.f(f"fk_{table}_space_id_spaces"), table, type_="foreignkey")
    op.drop_table("space_members")
    op.drop_index("uq_spaces_private_owner", table_name="spaces")
    op.drop_index("ix_spaces_owner_user_id", table_name="spaces")
    op.drop_table("spaces")
    op.execute("DROP FUNCTION app_space_ids()")
    op.execute("DROP FUNCTION app_user_id()")
    bind = op.get_bind()
    for name in reversed(list(ENUMS)):
        _enum(name).drop(bind, checkfirst=False)
