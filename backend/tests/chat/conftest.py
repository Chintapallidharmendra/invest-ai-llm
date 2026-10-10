"""Fixtures for the conversation tests (Story 3.1): spaces (users, workspaces), the
auth environment for API tests, and helpers for raw SQL under a user's RLS context."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.auth.models import User
from app.auth.service import SessionUser
from app.spaces.access import AccessContext
from app.spaces.deps import build_access_context
from tests.audit.conftest import audit_key
from tests.auth.conftest import auth_env, auth_settings_env, csrf_headers, login, make_client
from tests.crypto.conftest import kek_bytes
from tests.spaces.conftest import spaces_db

__all__ = ["audit_key", "auth_env", "auth_settings_env", "kek_bytes", "spaces_db"]


@asynccontextmanager
async def signed_in(username: str) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client() as client:
        assert (await login(client, username)).status_code == 200
        client.headers.update(csrf_headers(client))
        yield client


async def context_of(user: User) -> AccessContext:
    """The context a request by ``user`` would get (memberships read now)."""
    return await build_access_context(SessionUser(user.id, user.username, user.role, user.id))
