"""Identity tables: users, sessions, password_tokens (ADR-006, ADR-023).

Revision ID: 2_1_auth_tables
Revises: 1_11_jobs
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "2_1_auth_tables"
down_revision: str | Sequence[str] | None = "1_11_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUMS = {
    "user_role": ("user", "admin", "compliance"),
    "user_status": ("invited", "active", "locked", "deactivated"),
    "password_token_purpose": ("invite", "reset"),
}


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def _ts(name: str, *, nullable: bool = False, default: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()") if default else None,
        nullable=nullable,
    )


def _pk() -> sa.Column[object]:
    return sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False)


def upgrade() -> None:
    bind = op.get_bind()
    for name in ENUMS:
        _enum(name).create(bind, checkfirst=False)

    op.create_table(
        "users",
        _pk(),
        sa.Column("username", postgresql.CITEXT(), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("role", _enum("user_role"), nullable=False),
        sa.Column("status", _enum("user_status"), server_default="invited", nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("failed_login_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _ts("locked_until", nullable=True),
        _ts("last_login_at", nullable=True),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.CheckConstraint(
            "char_length(username) BETWEEN 1 AND 64", name=op.f("ck_users_username_length")
        ),
        sa.CheckConstraint(
            "char_length(email) BETWEEN 3 AND 254", name=op.f("ck_users_email_length")
        ),
        sa.CheckConstraint(
            "failed_login_count >= 0", name=op.f("ck_users_failed_login_count_non_negative")
        ),
        sa.CheckConstraint(
            "status = 'invited' OR password_hash IS NOT NULL",
            name=op.f("ck_users_password_unless_invited"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )

    op.create_table(
        "sessions",
        _pk(),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        _ts("created_at", default=True),
        _ts("last_seen_at", default=True),
        _ts("expires_at"),
        _ts("revoked_at", nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "octet_length(token_hash) = 32", name=op.f("ck_sessions_token_hash_length")
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_sessions_expires_after_created")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sessions_token_hash")),
    )
    op.create_index(
        "ix_sessions_user_id_active",
        "sessions",
        ["user_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "password_tokens",
        _pk(),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("purpose", _enum("password_token_purpose"), nullable=False),
        _ts("created_at", default=True),
        _ts("expires_at"),
        _ts("used_at", nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.CheckConstraint(
            "octet_length(token_hash) = 32", name=op.f("ck_password_tokens_token_hash_length")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_password_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_password_tokens_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_password_tokens_token_hash")),
    )
    op.create_index(
        op.f("ix_password_tokens_user_id"), "password_tokens", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_password_tokens_user_id"), table_name="password_tokens")
    op.drop_table("password_tokens")
    op.drop_index("ix_sessions_user_id_active", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("users")
    bind = op.get_bind()
    for name in reversed(ENUMS):
        postgresql.ENUM(name=name).drop(bind, checkfirst=False)
