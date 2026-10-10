"""Fixtures for the admin API tests (Story 2.3): the auth environment (database,
Valkey, audit key) plus a KEK, since creating a user creates their private space key."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

import httpx
import pytest

from app.admin.settings import get_admin_settings
from app.auth.models import Role, User
from tests.audit.conftest import audit_key
from tests.auth.conftest import (
    AuthEnv,
    auth_env,
    auth_settings_env,
    csrf_headers,
    login,
    make_client,
)
from tests.crypto.conftest import kek_bytes
from tests.spaces.conftest import make_user

__all__ = ["audit_key", "auth_env", "auth_settings_env", "kek_bytes"]

SITE_URL: Final = "https://invest-ai.test"


@pytest.fixture
async def admin_env(
    auth_env: AuthEnv, kek_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AuthEnv]:
    monkeypatch.setenv("APP_ADMIN_SITE_URL", SITE_URL)
    get_admin_settings.cache_clear()
    yield auth_env
    get_admin_settings.cache_clear()


@pytest.fixture
async def admin(admin_env: AuthEnv) -> User:
    return await make_user("root", role=Role.ADMIN)


@pytest.fixture
async def admin_client(admin: User) -> AsyncIterator[httpx.AsyncClient]:
    """Signed in as ``admin``, with the CSRF header set on every request."""
    async with make_client() as client:
        assert (await login(client, admin.username)).status_code == 200
        client.headers.update(csrf_headers(client))
        yield client


@asynccontextmanager
async def signed_in(username: str) -> AsyncIterator[httpx.AsyncClient]:
    """A client signed in as ``username``, with the CSRF header set."""
    async with make_client() as client:
        response = await login(client, username)
        assert response.status_code == 200, response.text
        client.headers.update(csrf_headers(client))
        yield client


def token_of(url: str) -> str:
    assert url.startswith(f"{SITE_URL}/set-password#t=")
    return url.split("#t=", 1)[1]
