"""Row-level security for content tables (ADR-023). **LOCKED:** every content table
has ``space_id`` and is created with :func:`enable_space_rls` in its migration::

    from app.spaces.rls import disable_space_rls, enable_space_rls

    def upgrade() -> None:
        op.create_table("documents", ..., sa.Column("space_id", sa.UUID(), nullable=False))
        enable_space_rls(op, "documents")
        # owner-only even inside a workspace:
        enable_space_rls(op, "conversations", owner_column="owner_user_id")

    def downgrade() -> None:
        disable_space_rls(op, "documents")
        op.drop_table("documents")

The policy admits a row when its ``space_id`` is one of the open spaces the current
user belongs to (``app_space_ids()``), and, with ``owner_column``, only if that column
is the current user (``app_user_id()``). Both functions read ``app.user_id``, which
``core.db.transaction(context=AccessContext.rls_settings())`` sets for the transaction.
Without it every policy admits nothing. ``FORCE`` makes the policy apply to the table
owner (``app_migrator``) too.

``spaces`` and ``space_members`` themselves are the exception: they are read by the
``SECURITY DEFINER`` function ``app_space_ids()`` as their owner, so they ENABLE but
don't FORCE RLS (forcing would make the function recurse into its own policy).
``app_rw`` never owns a table, so their policies always apply to the application.
"""

import re
from typing import Final, Protocol

_IDENTIFIER: Final = re.compile(r"[a-z_][a-z0-9_]{0,62}")


class _Operations(Protocol):
    def execute(self, sqltext: str) -> object: ...


def _ident(name: str) -> str:
    if not _IDENTIFIER.fullmatch(name):
        raise ValueError(f"not a plain lower-case SQL identifier: {name!r}")
    return name


def policy_name(table: str) -> str:
    return f"{_ident(table)}_space_isolation"


def space_rls_statements(
    table: str, *, owner_column: str | None = None, space_column: str = "space_id"
) -> list[str]:
    """The SQL that :func:`enable_space_rls` runs (exposed for tests)."""
    table, space_column = _ident(table), _ident(space_column)
    condition = f"{space_column} = ANY ((SELECT app_space_ids())::uuid[])"
    if owner_column is not None:
        condition += f" AND {_ident(owner_column)} = (SELECT app_user_id())"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {policy_name(table)} ON {table} "
        f"USING ({condition}) WITH CHECK ({condition})",
    ]


def disable_space_rls_statements(table: str) -> list[str]:
    table = _ident(table)
    return [
        f"DROP POLICY IF EXISTS {policy_name(table)} ON {table}",
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
    ]


def enable_space_rls(
    op: _Operations,
    table: str,
    *,
    owner_column: str | None = None,
    space_column: str = "space_id",
) -> None:
    """ENABLE + FORCE RLS on ``table`` with the standard space (and owner) policy."""
    for statement in space_rls_statements(
        table, owner_column=owner_column, space_column=space_column
    ):
        op.execute(statement)


def disable_space_rls(op: _Operations, table: str) -> None:
    """Undo :func:`enable_space_rls` (for downgrades)."""
    for statement in disable_space_rls_statements(table):
        op.execute(statement)
