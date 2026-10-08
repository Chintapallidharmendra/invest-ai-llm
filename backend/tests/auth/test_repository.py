"""AC #1 / #2: identity tables (migration, constraints) and repository round-trips."""

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth import repository as repo
from app.auth.models import Role, TokenPurpose, User, UserStatus
from app.auth.passwords import hash_password
from app.auth.tokens import generate_token, hash_token
from app.core.db import transaction
from tests.conftest import BACKEND_DIR, PgDatabase

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHRzYWx0$aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGFzaA"


async def _user(name: str, *, role: Role = Role.USER, **kw: object) -> User:
    async with transaction() as tx:
        return await repo.create_user(
            tx,
            username=name,
            email=f"{name}@example.test",
            role=role,
            **kw,  # type: ignore[arg-type]
        )


async def _scalar(url: str, sql: str) -> object:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql))).scalar()
    finally:
        await engine.dispose()


def _alembic(pg: PgDatabase, *args: str) -> None:
    result = subprocess.run(  # noqa: S603 (fixed argv)
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env={**os.environ, "APP_MIGRATOR_DATABASE_URL": pg.migrator_url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]


# --- Migration and constraints ---------------------------------------------------------


async def test_migration_down_and_up(pg: PgDatabase) -> None:
    _alembic(pg, "downgrade", "1_11_jobs")
    try:
        for table in ("users", "sessions", "password_tokens"):
            assert await _scalar(pg.migrator_url, f"SELECT to_regclass('public.{table}')") is None
        enums = await _scalar(
            pg.migrator_url,
            "SELECT count(*) FROM pg_type WHERE typname IN "
            "('user_role', 'user_status', 'password_token_purpose')",
        )
        assert enums == 0
    finally:
        _alembic(pg, "upgrade", "head")
    for table in ("users", "sessions", "password_tokens"):
        assert (
            await _scalar(pg.migrator_url, f"SELECT to_regclass('public.{table}')::text") == table
        )


async def test_schema_details(pg: PgDatabase) -> None:
    index = await _scalar(
        pg.migrator_url,
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_sessions_user_id_active'",
    )
    assert "(user_id) WHERE (revoked_at IS NULL)" in str(index)
    types = await _scalar(
        pg.migrator_url,
        "SELECT string_agg(table_name || '.' || column_name || ':' || udt_name, ',' "
        "ORDER BY table_name, column_name) FROM information_schema.columns "
        "WHERE table_name IN ('users', 'sessions', 'password_tokens') AND column_name IN "
        "('username', 'email', 'role', 'status', 'token_hash', 'ip', 'purpose', 'id')",
    )
    assert types == (
        "password_tokens.id:uuid,password_tokens.purpose:password_token_purpose,"
        "password_tokens.token_hash:bytea,sessions.id:uuid,sessions.ip:inet,"
        "sessions.token_hash:bytea,users.email:citext,users.id:uuid,users.role:user_role,"
        "users.status:user_status,users.username:citext"
    )
    rls = await _scalar(
        pg.migrator_url,
        "SELECT bool_or(relrowsecurity) FROM pg_class "
        "WHERE relname IN ('users', 'sessions', 'password_tokens')",
    )
    assert rls is False


async def test_username_and_email_are_unique_case_insensitively(pg: PgDatabase) -> None:
    await _user("Asha")
    with pytest.raises(repo.DuplicateUserError) as info:
        async with transaction() as tx:
            await repo.create_user(tx, username="ASHA", email="other@example.test", role=Role.USER)
    assert info.value.field == "username"
    with pytest.raises(repo.DuplicateUserError) as info:
        async with transaction() as tx:
            await repo.create_user(tx, username="ravi", email="ASHA@Example.TEST", role=Role.USER)
    assert info.value.field == "email"


async def test_duplicate_keeps_the_transaction_usable(pg: PgDatabase) -> None:
    await _user("asha")
    async with transaction() as tx:
        with pytest.raises(repo.DuplicateUserError):
            await repo.create_user(tx, username="asha", email="x@example.test", role=Role.USER)
        ravi = await repo.create_user(tx, username="ravi", email="r@example.test", role=Role.USER)
    async with transaction() as tx:
        assert await repo.get_user_by_id(tx, ravi.id) is not None


@pytest.mark.parametrize(
    ("username", "email", "status", "password_hash"),
    [
        ("", "e@example.test", UserStatus.INVITED, None),
        ("u" * 65, "e@example.test", UserStatus.INVITED, None),
        ("bob", "e@example.test", UserStatus.ACTIVE, None),  # active needs a password
    ],
)
async def test_user_check_constraints(
    pg: PgDatabase, username: str, email: str, status: UserStatus, password_hash: str | None
) -> None:
    with pytest.raises(IntegrityError):
        async with transaction() as tx:
            await repo.create_user(
                tx,
                username=username,
                email=email,
                role=Role.USER,
                status=status,
                password_hash=password_hash,
            )


async def test_session_ip_is_validated(pg: PgDatabase) -> None:
    user = await _user("asha")
    async with transaction() as tx:
        with pytest.raises(ValueError, match="does not appear to be an IPv4 or IPv6"):
            await repo.create_session(
                tx,
                user_id=user.id,
                token_hash=hash_token(generate_token()),
                expires_at=NOW + timedelta(hours=1),
                ip="not-an-ip",
                now=NOW,
            )
        row = await repo.create_session(
            tx,
            user_id=user.id,
            token_hash=hash_token(generate_token()),
            expires_at=NOW + timedelta(hours=1),
            ip="2001:db8::1",
            now=NOW,
        )
    assert row.ip == ip_address("2001:db8::1")


async def test_token_hash_must_be_32_bytes(pg: PgDatabase) -> None:
    user = await _user("asha")
    with pytest.raises(IntegrityError):
        async with transaction() as tx:
            await repo.create_session(
                tx, user_id=user.id, token_hash=b"short", expires_at=NOW + timedelta(hours=12)
            )


# --- Users -------------------------------------------------------------------------------


async def test_user_round_trip(pg: PgDatabase) -> None:
    created = await _user("Asha.Rao", role=Role.COMPLIANCE)
    assert created.id.version == 7
    assert (created.status, created.failed_login_count, created.password_hash) == (
        UserStatus.INVITED,
        0,
        None,
    )
    async with transaction() as tx:
        by_id = await repo.get_user_by_id(tx, created.id)
        by_name = await repo.get_user_by_username(tx, "asha.rao")
        by_email = await repo.get_user_by_email(tx, "ASHA.RAO@example.test")
        assert await repo.get_user_by_username(tx, "nobody") is None
        assert await repo.get_user_by_email(tx, "nobody@example.test") is None
        assert await repo.get_user_by_id(tx, uuid.uuid4()) is None
    assert by_id is not None
    assert by_name is not None
    assert by_email is not None
    assert by_id.id == by_name.id == by_email.id == created.id
    assert by_id.username == "Asha.Rao"  # stored as typed
    assert by_id.role is Role.COMPLIANCE


async def test_status_role_password_and_counters(pg: PgDatabase) -> None:
    user = await _user("asha")
    later = NOW + timedelta(minutes=5)
    async with transaction() as tx:
        assert await repo.set_password_hash(tx, user.id, HASH, now=NOW)
        assert await repo.update_user_status(tx, user.id, UserStatus.ACTIVE, now=NOW)
        assert await repo.update_user_role(tx, user.id, Role.ADMIN, now=NOW)
        assert await repo.increment_failed_logins(tx, user.id) == 1
        assert await repo.increment_failed_logins(tx, user.id) == 2
        assert await repo.set_locked_until(tx, user.id, later, now=NOW)
        assert not await repo.update_user_status(tx, uuid.uuid4(), UserStatus.LOCKED)
        assert await repo.increment_failed_logins(tx, uuid.uuid4()) is None
    async with transaction() as tx:
        row = await repo.get_user_by_id(tx, user.id)
        assert row is not None
        assert (row.password_hash, row.status, row.role) == (HASH, UserStatus.ACTIVE, Role.ADMIN)
        assert (row.failed_login_count, row.locked_until) == (2, later)
        assert await repo.record_successful_login(tx, user.id, now=later)
    async with transaction() as tx:
        row = await repo.get_user_by_id(tx, user.id)
        assert row is not None
        assert (row.failed_login_count, row.locked_until, row.last_login_at) == (0, None, later)
        assert row.updated_at == later


async def test_real_password_hash_is_stored(pg: PgDatabase) -> None:
    hashed = hash_password("violet otter juggles teacups")
    user = await _user("asha", status=UserStatus.ACTIVE, password_hash=hashed)
    async with transaction() as tx:
        row = await repo.get_user_by_id(tx, user.id)
    assert row is not None
    assert row.password_hash == hashed


# --- Sessions ----------------------------------------------------------------------------


async def test_session_round_trip(pg: PgDatabase) -> None:
    user = await _user("asha")
    token = generate_token()
    async with transaction() as tx:
        created = await repo.create_session(
            tx,
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=NOW + timedelta(hours=12),
            ip="10.0.0.7",
            user_agent="Firefox/140",
            now=NOW,
        )
    async with transaction() as tx:
        found = await repo.get_session_by_hash(tx, hash_token(token))
        assert await repo.get_session_by_hash(tx, hash_token(generate_token())) is None
    assert found is not None
    assert found.id == created.id
    assert (found.user_id, found.ip, found.user_agent) == (
        user.id,
        ip_address("10.0.0.7"),
        "Firefox/140",
    )
    assert (found.created_at, found.last_seen_at, found.revoked_at) == (NOW, NOW, None)
    assert found.is_active(NOW + timedelta(hours=11))
    assert not found.is_active(NOW + timedelta(hours=12))

    async with transaction() as tx:
        assert await repo.touch_session(tx, created.id, now=NOW + timedelta(minutes=3))
        assert await repo.revoke_session(tx, created.id, now=NOW + timedelta(minutes=4))
        assert not await repo.revoke_session(tx, created.id)  # already revoked
        assert not await repo.touch_session(tx, created.id)  # revoked: not touched
    async with transaction() as tx:
        found = await repo.get_session_by_hash(tx, hash_token(token))
    assert found is not None
    assert found.last_seen_at == NOW + timedelta(minutes=3)
    assert found.revoked_at == NOW + timedelta(minutes=4)
    assert not found.is_active(NOW + timedelta(minutes=5))


async def test_raw_token_is_never_stored(pg: PgDatabase) -> None:
    user = await _user("asha")
    token = generate_token()
    async with transaction() as tx:
        await repo.create_session(
            tx,
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=NOW + timedelta(hours=1),
            now=NOW,
        )
        await repo.create_password_token(
            tx,
            user_id=user.id,
            token_hash=hash_token(token + "x"),
            purpose=TokenPurpose.INVITE,
            expires_at=NOW + timedelta(hours=24),
        )
        dump = (
            await tx.execute(
                text(
                    "SELECT string_agg(t, ' ') FROM (SELECT s::text AS t FROM sessions s "
                    "UNION ALL SELECT p::text FROM password_tokens p) x"
                )
            )
        ).scalar()
    assert token not in str(dump)


async def test_revoke_all_affects_only_the_target_user(pg: PgDatabase) -> None:
    asha, ravi = await _user("asha"), await _user("ravi")
    expires = NOW + timedelta(hours=12)
    async with transaction() as tx:
        asha_sessions = [
            await repo.create_session(
                tx,
                user_id=asha.id,
                token_hash=hash_token(generate_token()),
                expires_at=expires,
                now=NOW,
            )
            for _ in range(3)
        ]
        ravi_session = await repo.create_session(
            tx,
            user_id=ravi.id,
            token_hash=hash_token(generate_token()),
            expires_at=expires,
            now=NOW,
        )
    async with transaction() as tx:
        keep = asha_sessions[0].id
        assert await repo.revoke_all_sessions(tx, asha.id, except_session_id=keep, now=NOW) == 2
        assert await repo.revoke_all_sessions(tx, asha.id, now=NOW) == 1
        assert await repo.revoke_all_sessions(tx, asha.id, now=NOW) == 0
        revoked = (
            await tx.execute(
                text("SELECT count(*) FROM sessions WHERE user_id = :u AND revoked_at IS NOT NULL"),
                {"u": asha.id},
            )
        ).scalar()
        other = await repo.get_session_by_hash(tx, ravi_session.token_hash)
    assert revoked == 3
    assert other is not None
    assert other.revoked_at is None


# --- Set-password tokens ------------------------------------------------------------------


async def test_password_token_lifecycle(pg: PgDatabase) -> None:
    admin, asha = await _user("admin1", role=Role.ADMIN), await _user("asha")
    token = generate_token()
    async with transaction() as tx:
        created = await repo.create_password_token(
            tx,
            user_id=asha.id,
            token_hash=hash_token(token),
            purpose=TokenPurpose.INVITE,
            expires_at=NOW + timedelta(hours=24),
            created_by=admin.id,
            now=NOW,
        )
    async with transaction() as tx:
        valid = await repo.get_valid_password_token(tx, hash_token(token), now=NOW)
        assert valid is not None
        assert (valid.id, valid.user_id, valid.created_by) == (created.id, asha.id, admin.id)
        assert valid.purpose is TokenPurpose.INVITE
        assert (
            await repo.get_valid_password_token(
                tx, hash_token(token), purpose=TokenPurpose.RESET, now=NOW
            )
            is None
        )
        # Expired at exactly expires_at.
        assert (
            await repo.get_valid_password_token(
                tx, hash_token(token), now=NOW + timedelta(hours=24)
            )
            is None
        )
        assert await repo.mark_password_token_used(tx, created.id, now=NOW + timedelta(hours=1))
        assert not await repo.mark_password_token_used(tx, created.id)  # single use
        assert await repo.get_valid_password_token(tx, hash_token(token), now=NOW) is None


async def test_invalidate_tokens_affects_only_the_target_user(pg: PgDatabase) -> None:
    asha, ravi = await _user("asha"), await _user("ravi")
    expires = NOW + timedelta(hours=24)

    async def issue(user: User, purpose: TokenPurpose) -> bytes:
        token_hash = hash_token(generate_token())
        async with transaction() as tx:
            await repo.create_password_token(
                tx,
                user_id=user.id,
                token_hash=token_hash,
                purpose=purpose,
                expires_at=expires,
                now=NOW,
            )
        return token_hash

    asha_invite = await issue(asha, TokenPurpose.INVITE)
    asha_reset = await issue(asha, TokenPurpose.RESET)
    ravi_reset = await issue(ravi, TokenPurpose.RESET)
    later = NOW + timedelta(hours=1)
    async with transaction() as tx:
        assert (
            await repo.invalidate_password_tokens(
                tx, asha.id, purpose=TokenPurpose.RESET, now=later
            )
            == 1
        )
        assert await repo.get_valid_password_token(tx, asha_invite, now=later) is not None
        assert await repo.invalidate_password_tokens(tx, asha.id, now=later) == 1
        assert await repo.invalidate_password_tokens(tx, asha.id, now=later) == 0
        for token_hash in (asha_invite, asha_reset):
            assert await repo.get_valid_password_token(tx, token_hash, now=later) is None
        assert await repo.get_valid_password_token(tx, ravi_reset, now=later) is not None
        used = (
            await tx.execute(text("SELECT count(*) FROM password_tokens WHERE used_at IS NOT NULL"))
        ).scalar()
    assert used == 0  # invalidated, not "used"


async def test_deleting_a_user_cascades_and_nulls_created_by(pg: PgDatabase) -> None:
    admin, asha = await _user("admin1", role=Role.ADMIN), await _user("asha")
    async with transaction() as tx:
        await repo.create_session(
            tx,
            user_id=asha.id,
            token_hash=hash_token(generate_token()),
            expires_at=NOW + timedelta(hours=1),
            now=NOW,
        )
        token = await repo.create_password_token(
            tx,
            user_id=asha.id,
            token_hash=hash_token(generate_token()),
            purpose=TokenPurpose.INVITE,
            expires_at=NOW + timedelta(hours=1),
            created_by=admin.id,
        )
        ravi = await repo.create_user(
            tx, username="ravi", email="ravi@example.test", role=Role.USER
        )
        ravi_token = await repo.create_password_token(
            tx,
            user_id=ravi.id,
            token_hash=hash_token(generate_token()),
            purpose=TokenPurpose.INVITE,
            expires_at=NOW + timedelta(hours=1),
            created_by=admin.id,
        )
    async with transaction() as tx:
        await tx.execute(text("DELETE FROM users WHERE id = :u"), {"u": asha.id})
        await tx.execute(text("DELETE FROM users WHERE id = :u"), {"u": admin.id})
        sessions = (await tx.execute(text("SELECT count(*) FROM sessions"))).scalar()
        gone = (
            await tx.execute(
                text("SELECT count(*) FROM password_tokens WHERE id = :t"), {"t": token.id}
            )
        ).scalar()
        created_by = (
            await tx.execute(
                text("SELECT created_by FROM password_tokens WHERE id = :t"), {"t": ravi_token.id}
            )
        ).scalar()
    assert (sessions, gone, created_by) == (0, 0, None)
