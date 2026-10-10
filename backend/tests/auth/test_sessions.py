"""AC #3 / #5: session checks on every request, require_role, and logout."""

from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
from fastapi import Depends, FastAPI

from app.auth import repository, service
from app.auth.deps import CurrentUser, require_role
from app.auth.models import Role, UserSession, UserStatus
from app.auth.tokens import hash_token
from app.core.db import transaction
from app.core.errors import install_exception_handlers
from app.core.timeutil import utcnow
from tests.auth.conftest import (
    SITE,
    AuthEnv,
    audit_rows,
    create_user,
    csrf_headers,
    login,
    make_client,
    session_cookie,
)

pytestmark = pytest.mark.db


async def _logged_in(client: httpx.AsyncClient, name: str, **kw: object) -> str:
    await create_user(name, **kw)  # type: ignore[arg-type]
    response = await login(client, name)
    assert response.status_code == 200
    token = session_cookie(client)
    assert token is not None
    return token


async def _session_row(token: str) -> UserSession:
    async with transaction() as tx:
        row = await repository.get_session_by_hash(tx, hash_token(token))
        assert row is not None
        return row


def _not_authenticated(response: httpx.Response) -> None:
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


# --- AC #3: what makes a session invalid -----------------------------------------------


async def test_no_cookie_and_garbage_cookie(auth_client: httpx.AsyncClient) -> None:
    _not_authenticated(await auth_client.get("/api/v1/auth/me"))
    async with make_client(cookies={"__Host-session": "not-a-real-token"}) as client:
        _not_authenticated(await client.get("/api/v1/auth/me"))


async def test_revoked_session(auth_client: httpx.AsyncClient) -> None:
    token = await _logged_in(auth_client, "rev")
    row = await _session_row(token)
    async with transaction() as tx:
        await repository.revoke_session(tx, row.id)
    _not_authenticated(await auth_client.get("/api/v1/auth/me"))


async def test_deactivated_user(auth_client: httpx.AsyncClient) -> None:
    token = await _logged_in(auth_client, "deact")
    row = await _session_row(token)
    async with transaction() as tx:
        await repository.update_user_status(tx, row.user_id, UserStatus.DEACTIVATED)
    _not_authenticated(await auth_client.get("/api/v1/auth/me"))


async def test_absolute_and_idle_expiry(auth_client: httpx.AsyncClient) -> None:
    token = await _logged_in(auth_client, "expiry")
    row = await _session_row(token)
    start = row.last_seen_at
    # Keep the session busy (touched every 50 min) right up to the 12 h absolute limit.
    now = start
    while now + timedelta(minutes=50) < row.expires_at:
        now += timedelta(minutes=50)
        assert await service.authenticate(token, now=now) is not None
    assert await service.authenticate(token, now=row.expires_at) is None
    assert await service.authenticate(token, now=row.expires_at + timedelta(seconds=1)) is None


async def test_idle_timeout(auth_client: httpx.AsyncClient) -> None:
    token = await _logged_in(auth_client, "idle")
    row = await _session_row(token)
    assert await service.authenticate(token, now=row.last_seen_at + timedelta(minutes=60))
    other = await _logged_in(auth_client, "idle2")
    other_row = await _session_row(other)
    later = other_row.last_seen_at + timedelta(minutes=60, seconds=1)
    assert await service.authenticate(other, now=later) is None


async def test_touch_is_throttled(auth_client: httpx.AsyncClient) -> None:
    token = await _logged_in(auth_client, "touchy")
    seen = (await _session_row(token)).last_seen_at
    assert await service.authenticate(token, now=seen + timedelta(seconds=30))
    assert (await _session_row(token)).last_seen_at == seen  # no write within 60 s
    touched = seen + timedelta(seconds=61)
    assert await service.authenticate(token, now=touched)
    assert (await _session_row(token)).last_seen_at == touched


async def test_api_requests_touch_last_seen(
    auth_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = await _logged_in(auth_client, "busy")
    seen = (await _session_row(token)).last_seen_at
    later = utcnow() + timedelta(minutes=5)
    monkeypatch.setattr(service, "utcnow", lambda: later)
    assert (await auth_client.get("/api/v1/auth/me")).status_code == 200
    assert (await _session_row(token)).last_seen_at == later > seen


# --- AC #3: require_role -------------------------------------------------------------------


@pytest.fixture
async def role_client(auth_env: AuthEnv) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    install_exception_handlers(app)

    @app.get("/admin-only")
    async def admin_only(
        user: CurrentUser = Depends(require_role(Role.ADMIN)),  # noqa: B008
    ) -> dict[str, str]:
        return {"role": user.role.value}

    @app.get("/staff")
    async def staff(
        user: CurrentUser = Depends(require_role(Role.ADMIN, Role.COMPLIANCE)),  # noqa: B008
    ) -> dict[str, str]:
        return {"role": user.role.value}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=SITE) as client:
        yield client


async def test_require_role(auth_client: httpx.AsyncClient, role_client: httpx.AsyncClient) -> None:
    user_token = await _logged_in(auth_client, "plain")
    role_client.cookies.set("__Host-session", user_token)
    denied = await role_client.get("/admin-only")
    assert denied.status_code == 403
    assert denied.json()["code"] == "forbidden"
    [event] = await audit_rows("auth.access_denied")
    assert event.payload == {"role": "user"}
    assert event.actor_user_id == (await _session_row(user_token)).user_id

    admin_token = await _logged_in(auth_client, "boss", role=Role.ADMIN)
    role_client.cookies.set("__Host-session", admin_token)
    assert (await role_client.get("/admin-only")).json() == {"role": "admin"}
    compliance_token = await _logged_in(auth_client, "audit", role=Role.COMPLIANCE)
    role_client.cookies.set("__Host-session", compliance_token)
    assert (await role_client.get("/staff")).status_code == 200
    assert (await role_client.get("/admin-only")).status_code == 403

    role_client.cookies.clear()
    assert (await role_client.get("/admin-only")).status_code == 401


def test_require_role_needs_a_role() -> None:
    with pytest.raises(ValueError, match="at least one"):
        require_role()


# --- AC #5: logout ------------------------------------------------------------------------


async def test_logout_revokes_and_expires_cookies(auth_client: httpx.AsyncClient) -> None:
    token = await _logged_in(auth_client, "leaver")
    response = await auth_client.post("/api/v1/auth/logout", headers=csrf_headers(auth_client))
    assert response.status_code == 204
    assert response.headers.get_list("set-cookie") == [
        "__Host-session=; Max-Age=0; HttpOnly; Secure; SameSite=Strict; Path=/",
        "__Host-csrf=; Max-Age=0; Secure; SameSite=Strict; Path=/",
    ]
    assert (await _session_row(token)).revoked_at is not None
    [event] = await audit_rows("auth.logged_out")
    assert event.payload == {"session_id": str((await _session_row(token)).id)}

    async with make_client(cookies={"__Host-session": token}) as reuse:
        _not_authenticated(await reuse.get("/api/v1/auth/me"))


async def test_logout_needs_csrf_and_a_session(auth_client: httpx.AsyncClient) -> None:
    await _logged_in(auth_client, "csrfless")
    response = await auth_client.post("/api/v1/auth/logout")
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"
    async with make_client() as anonymous:
        anonymous.cookies.set("__Host-csrf", "t")
        response = await anonymous.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "t"})
    _not_authenticated(response)


async def test_logout_only_ends_that_session(auth_env: AuthEnv) -> None:
    await create_user("two")
    async with make_client() as first, make_client() as second:
        await login(first, "two")
        await login(second, "two")
        await first.post("/api/v1/auth/logout", headers=csrf_headers(first))
        assert (await first.get("/api/v1/auth/me")).status_code == 401
        assert (await second.get("/api/v1/auth/me")).status_code == 200
