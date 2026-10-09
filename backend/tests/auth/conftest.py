"""Fixtures for the login/session tests (Story 2.2).

``auth_client`` is an httpx client over HTTPS (the ``__Host-`` cookies are ``Secure``)
against the real app, with the test database, Valkey and an audit key. It sends the
site's ``Origin`` by default, as a browser would; CSRF tests override it.
"""

import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any, Final

import httpx
import pytest
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.app import create_app
from app.audit.models import AuditEventRow
from app.auth import repository
from app.auth.csrf import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from app.auth.models import Role, User, UserStatus
from app.auth.passwords import hash_password, normalise
from app.auth.settings import get_auth_settings
from app.core.config import get_settings
from app.core.db import transaction
from app.jobs.settings import get_jobs_settings
from tests.audit.conftest import audit_key
from tests.conftest import PgDatabase

__all__ = ["audit_key"]

SITE: Final = "https://testserver"
PASSWORD: Final = "correct horse battery staple 42"  # noqa: S105 (test credential)


@dataclass(frozen=True)
class AuthEnv:
    pg: PgDatabase
    valkey_url: str


@pytest.fixture
def auth_settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Env overrides for AuthSettings; caches are cleared before and after."""
    monkeypatch.setenv("APP_AUTH_SITE_ORIGINS", f'["{SITE}"]')
    get_auth_settings.cache_clear()
    yield monkeypatch
    get_auth_settings.cache_clear()


@pytest.fixture
async def auth_env(
    pg: PgDatabase,
    _valkey_url: str,
    valkey: Any,
    audit_key: bytes,
    auth_settings_env: pytest.MonkeyPatch,
) -> AsyncIterator[AuthEnv]:
    auth_settings_env.setenv("APP_VALKEY_URL", _valkey_url)
    get_jobs_settings.cache_clear()
    yield AuthEnv(pg, _valkey_url)
    get_jobs_settings.cache_clear()


def set_auth_env(monkeypatch: pytest.MonkeyPatch, **values: object) -> None:
    for key, value in values.items():
        monkeypatch.setenv(f"APP_AUTH_{key.upper()}", str(value))
    get_auth_settings.cache_clear()


def make_client(*, ip: str = "127.0.0.1", **kwargs: Any) -> httpx.AsyncClient:
    app = create_app(configure_logs=False, readiness_checks=False)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(ip, 50000)),
        base_url=SITE,
        headers={"Origin": SITE, **kwargs.pop("headers", {})},
        **kwargs,
    )


@pytest.fixture
async def auth_client(auth_env: AuthEnv) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client() as client:
        yield client
    get_settings.cache_clear()


async def create_user(
    username: str,
    *,
    password: str = PASSWORD,
    role: Role = Role.USER,
    status: UserStatus = UserStatus.ACTIVE,
    password_hash: str | None = None,
) -> User:
    async with transaction() as tx:
        return await repository.create_user(
            tx,
            username=username,
            email=f"{uuid.uuid4().hex[:8]}@example.test",
            role=role,
            status=status,
            password_hash=password_hash
            if password_hash is not None
            else (None if status is UserStatus.INVITED else hash_password(password)),
        )


def weak_hash(password: str) -> str:
    """An Argon2id hash with weaker-than-current parameters (needs a rehash)."""
    return Argon2Hasher(time_cost=1, memory_cost=8 * 1024, parallelism=1).hash(normalise(password))


async def get_user(user_id: uuid.UUID) -> User:
    async with transaction() as tx:
        user = await repository.get_user_by_id(tx, user_id)
        assert user is not None
        return user


async def login(
    client: httpx.AsyncClient, username: str, password: str = PASSWORD
) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )


def csrf_headers(client: httpx.AsyncClient) -> dict[str, str]:
    return {CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""}


def session_cookie(client: httpx.AsyncClient) -> str | None:
    return client.cookies.get(SESSION_COOKIE)


async def audit_rows(event_type: str | None = None) -> list[AuditEventRow]:
    async with transaction() as tx:
        stmt = select(AuditEventRow).order_by(AuditEventRow.seq)
        if event_type is not None:
            stmt = stmt.where(AuditEventRow.event_type == event_type)
        return list((await tx.execute(stmt)).scalars())


async def migrator_scalar(pg: PgDatabase, sql: str, params: dict[str, Any] | None = None) -> Any:
    engine = create_async_engine(pg.migrator_url)
    try:
        async with engine.begin() as conn:
            return (await conn.execute(text(sql), params or {})).scalar()
    finally:
        await engine.dispose()
