"""AC #1 / #2: login, failures, lockout and the per-IP rate limit."""

import asyncio
import re
import statistics
import time
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.auth import passwords, repository, service
from app.auth.models import Role, UserSession, UserStatus
from app.auth.tokens import hash_token
from app.core.db import transaction
from app.core.timeutil import utcnow
from app.jobs.settings import get_jobs_settings
from tests.auth.conftest import (
    PASSWORD,
    AuthEnv,
    audit_rows,
    create_user,
    get_user,
    login,
    make_client,
    session_cookie,
    set_auth_env,
    weak_hash,
)

pytestmark = pytest.mark.db

_SESSION_HEADER = re.compile(
    r"^__Host-session=([A-Za-z0-9_-]{43}); HttpOnly; Secure; SameSite=Strict; Path=/$"
)
_CSRF_HEADER = re.compile(r"^__Host-csrf=([A-Za-z0-9_-]{43}); Secure; SameSite=Strict; Path=/$")


def _without_correlation(response: httpx.Response) -> dict[str, Any]:
    body = dict(response.json())
    body.pop("correlation_id")
    return body


async def _sessions(user_id: Any) -> list[UserSession]:
    async with transaction() as tx:
        rows = await tx.execute(
            select(UserSession)
            .where(UserSession.user_id == user_id)
            .order_by(UserSession.created_at)
        )
        return list(rows.scalars())


# --- AC #1 ------------------------------------------------------------------------------


async def test_success_sets_both_cookies_and_returns_me(auth_client: httpx.AsyncClient) -> None:
    user = await create_user("asha", role=Role.ADMIN)
    response = await login(auth_client, "asha")
    assert response.status_code == 200
    assert response.json() == {"id": str(user.id), "username": "asha", "role": "admin"}

    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 2
    session = _SESSION_HEADER.match(cookies[0])
    csrf = _CSRF_HEADER.match(cookies[1])
    assert session is not None
    assert csrf is not None
    assert session.group(1) != csrf.group(1)

    [row] = await _sessions(user.id)
    assert row.token_hash == hash_token(session.group(1))  # only the hash is stored
    assert row.expires_at - row.created_at == timedelta(hours=12)
    refreshed = await get_user(user.id)
    assert refreshed.last_login_at is not None
    assert refreshed.failed_login_count == 0


async def test_me_after_login(auth_client: httpx.AsyncClient) -> None:
    user = await create_user("ravi")
    await login(auth_client, "ravi")
    response = await auth_client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json() == {"id": str(user.id), "username": "ravi", "role": "user"}


async def test_username_is_case_insensitive_and_nfkc(auth_client: httpx.AsyncClient) -> None:
    await create_user("Zoë")
    # Fullwidth Z plus "oe" and a combining diaeresis normalises (NFKC) to "Zoë";
    # citext ignores case.
    fullwidth = chr(0xFF3A) + "o" + "e" + chr(0x0308)
    assert (await login(auth_client, fullwidth)).status_code == 200
    assert (await login(auth_client, "  zoë ")).status_code == 200


async def test_weak_hash_is_upgraded(auth_client: httpx.AsyncClient) -> None:
    user = await create_user("old", password_hash=weak_hash(PASSWORD))
    assert passwords.needs_rehash((await get_user(user.id)).password_hash or "")
    assert (await login(auth_client, "old")).status_code == 200
    stored = (await get_user(user.id)).password_hash or ""
    assert not passwords.needs_rehash(stored)
    assert passwords.verify_password(PASSWORD, stored)
    [event] = await audit_rows("auth.login_succeeded")
    assert event.payload["rehashed"] is True


async def test_every_failure_kind_returns_the_same_401(auth_client: httpx.AsyncClient) -> None:
    await create_user("good")
    await create_user("gone", status=UserStatus.DEACTIVATED)
    await create_user("new", status=UserStatus.INVITED)
    locked = await create_user("locked")
    async with transaction() as tx:
        await repository.set_locked_until(tx, locked.id, utcnow() + timedelta(minutes=10))

    attempts = {
        "unknown": await login(auth_client, "nobody"),
        "wrong_password": await login(auth_client, "good", "not the password at all"),
        "locked": await login(auth_client, "locked"),
        "deactivated": await login(auth_client, "gone"),
        "invited": await login(auth_client, "new"),
        "too_long": await login(auth_client, "good", "x" * 1_000_000),
    }
    bodies = {kind: _without_correlation(r) for kind, r in attempts.items()}
    first = next(iter(bodies.values()))
    assert first["code"] == "invalid_credentials"
    assert first["status"] == 401
    for kind, response in attempts.items():
        assert response.status_code == 401, kind
        assert bodies[kind] == first, kind
        assert "set-cookie" not in response.headers
        assert response.headers["content-type"] == "application/problem+json"
    reasons = [row.payload["reason"] for row in await audit_rows("auth.login_failed")]
    assert reasons == [
        "unknown_user", "wrong_password", "locked", "deactivated", "invited", "too_long"
    ]  # fmt: skip


async def test_unknown_user_audit_has_only_a_keyed_hash(auth_client: httpx.AsyncClient) -> None:
    await login(auth_client, "Mallory")
    await login(auth_client, "mallory")
    first, second = await audit_rows("auth.login_failed")
    assert first.actor_user_id is None
    assert re.fullmatch(r"[0-9a-f]{64}", first.payload["username_hash"])
    assert first.payload["username_hash"] == second.payload["username_hash"]  # case-folded
    assert "allory" not in str(first.payload)


async def test_timing_unknown_vs_wrong_password(
    auth_env: AuthEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_auth_env(monkeypatch, login_rate_limit=1000, login_max_failures=1000)
    await create_user("timed")
    timings: dict[str, list[float]] = {"unknown": [], "wrong": []}
    async with make_client() as client:
        await login(client, "warmup", "warm up the hasher")
        for _ in range(9):
            for kind, name in (("unknown", "nobody-here"), ("wrong", "timed")):
                started = time.perf_counter()
                response = await login(client, name, "wrong password for timing")
                timings[kind].append(time.perf_counter() - started)
                assert response.status_code == 401
    unknown, wrong = (statistics.median(timings[k]) for k in ("unknown", "wrong"))
    assert abs(unknown - wrong) / max(unknown, wrong) < 0.30, timings


async def test_existing_session_cookie_is_rotated(auth_client: httpx.AsyncClient) -> None:
    user = await create_user("rotor")
    await login(auth_client, "rotor")
    old_token = session_cookie(auth_client)
    await login(auth_client, "rotor")
    new_token = session_cookie(auth_client)
    assert old_token is not None
    assert new_token != old_token
    first, second = await _sessions(user.id)
    assert first.revoked_at is not None
    assert second.revoked_at is None
    async with make_client(cookies={"__Host-session": old_token}) as stale:
        assert (await stale.get("/api/v1/auth/me")).status_code == 401
    [event, rotated] = await audit_rows("auth.login_succeeded")
    assert (event.payload["rotated_session"], rotated.payload["rotated_session"]) == (False, True)


async def test_concurrent_logins_each_get_a_session(auth_env: AuthEnv) -> None:
    user = await create_user("parallel")
    async with make_client() as a, make_client() as b, make_client() as c:
        responses = await asyncio.gather(*(login(x, "parallel") for x in (a, b, c)))
    assert [r.status_code for r in responses] == [200, 200, 200]
    assert len({s.token_hash for s in await _sessions(user.id)}) == 3


# --- AC #2: lockout -----------------------------------------------------------------------


async def test_fifth_failure_locks_for_15_minutes(
    auth_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await create_user("target")
    for attempt in range(1, 5):
        assert (await login(auth_client, "target", "wrong")).status_code == 401
        assert (await get_user(user.id)).locked_until is None, attempt
    started = utcnow()
    await login(auth_client, "target", "wrong")
    locked_until = (await get_user(user.id)).locked_until
    assert locked_until is not None
    assert (
        timedelta(minutes=14, seconds=59)
        < locked_until - started
        <= timedelta(minutes=15, seconds=5)
    )
    [locked] = await audit_rows("auth.account_locked")
    assert locked.payload["failed_count"] == 5
    assert locked.target_id == user.id

    # The right password fails during the lock, and doesn't extend it.
    assert (await login(auth_client, "target")).status_code == 401
    assert (await get_user(user.id)).locked_until == locked_until

    # After expiry (fake clock) the right password works and resets the counter.
    later = utcnow() + timedelta(minutes=16)
    monkeypatch.setattr(service, "utcnow", lambda: later)
    assert (await login(auth_client, "target")).status_code == 200
    after = await get_user(user.id)
    assert (after.failed_login_count, after.locked_until) == (0, None)


async def test_failure_after_an_expired_lock_starts_a_new_count(
    auth_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await create_user("again")
    for _ in range(5):
        await login(auth_client, "again", "wrong")
    later = utcnow() + timedelta(minutes=16)
    monkeypatch.setattr(service, "utcnow", lambda: later)
    await login(auth_client, "again", "wrong")
    after = await get_user(user.id)
    assert (after.failed_login_count, after.locked_until) == (1, None)


async def test_clock_change_during_the_lock(
    auth_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await create_user("clock")
    for _ in range(5):
        await login(auth_client, "clock", "wrong")
    # The clock jumps back an hour: still locked (the lock is an absolute time).
    earlier = utcnow() - timedelta(hours=1)
    monkeypatch.setattr(service, "utcnow", lambda: earlier)
    assert (await login(auth_client, "clock")).status_code == 401
    assert (await get_user(user.id)).locked_until is not None


async def test_success_resets_the_counter(auth_client: httpx.AsyncClient) -> None:
    user = await create_user("resets")
    for _ in range(4):
        await login(auth_client, "resets", "wrong")
    assert (await login(auth_client, "resets")).status_code == 200
    assert (await get_user(user.id)).failed_login_count == 0


# --- AC #2: rate limit ----------------------------------------------------------------------


async def test_eleventh_login_in_a_minute_is_rate_limited(auth_client: httpx.AsyncClient) -> None:
    for _ in range(10):
        assert (await login(auth_client, "nobody", "x")).status_code == 401
    response = await login(auth_client, "nobody", "x")
    assert response.status_code == 429
    body = response.json()
    assert body["code"] == "rate_limited"
    assert 1 <= int(response.headers["retry-after"]) <= 61
    assert body["retry_after_s"] == int(response.headers["retry-after"])
    # Even a valid login is refused while limited, and nothing reaches the database.
    before = len(await audit_rows())
    await create_user("blocked")
    assert (await login(auth_client, "blocked")).status_code == 429
    assert len(await audit_rows()) == before


async def test_rate_limit_is_per_ip_and_stores_no_ip(auth_env: AuthEnv, valkey: Any) -> None:
    async with make_client() as client:
        for _ in range(11):
            await login(client, "nobody", "x")
    keys = [k.decode() for k in await valkey.keys("*")]
    assert keys
    assert all(k.startswith("rl:login:") for k in keys)
    assert not any("127.0.0.1" in k for k in keys)
    async with make_client(ip="10.9.8.7") as elsewhere:
        assert (await login(elsewhere, "nobody", "x")).status_code == 401


async def test_rate_limiter_down_fails_closed(
    auth_env: AuthEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_VALKEY_URL", "valkey://127.0.0.1:1/0")
    get_jobs_settings.cache_clear()
    await create_user("someone")
    async with make_client() as client:
        response = await login(client, "someone")
    assert response.status_code == 503
    assert response.json()["code"] == "rate_limit_unavailable"
    assert "set-cookie" not in response.headers
