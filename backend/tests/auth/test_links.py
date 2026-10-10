"""Story 2.3 AC #2-#3: issuing and redeeming set-password links."""

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.audit.testing import assert_no_content
from app.auth import links, repository
from app.auth.models import PasswordToken, TokenPurpose, UserStatus
from app.auth.tokens import hash_token
from app.core.db import transaction
from app.core.timeutil import utcnow
from tests.auth.conftest import (
    PASSWORD,
    AuthEnv,
    audit_rows,
    create_user,
    get_user,
    login,
    make_client,
    session_cookie,
)
from tests.core.conftest import LogCapture, logs

__all__ = ["logs"]

pytestmark = pytest.mark.db

NEW_PASSWORD = "a brand new passphrase 2026"  # noqa: S105  gitleaks:allow (test value)
SET_PASSWORD = "/api/v1/auth/set-password"  # noqa: S105 (a URL path)


async def issue(user_id: uuid.UUID, **kwargs: object) -> links.IssuedToken:
    async with transaction() as tx:
        user = await repository.get_user_by_id(tx, user_id)
        assert user is not None
        return await links.issue_token(tx, user, created_by=None, **kwargs)  # type: ignore[arg-type]


async def redeem(
    token: str, username: str, password: str = NEW_PASSWORD
) -> tuple[int, dict[str, Any]]:
    async with make_client() as client:
        response = await client.post(
            SET_PASSWORD, json={"token": token, "username": username, "new_password": password}
        )
    return response.status_code, (response.json() if response.content else {})


async def test_invite_link_activates_user_and_allows_login(auth_env: AuthEnv) -> None:
    user = await create_user("nadia", status=UserStatus.INVITED)
    issued = await issue(user.id)
    assert issued.purpose is TokenPurpose.INVITE

    assert await redeem(issued.token, "Nadia") == (204, {})  # username is case-insensitive
    stored = await get_user(user.id)
    assert stored.status is UserStatus.ACTIVE
    async with make_client() as client:
        assert (await login(client, "nadia", NEW_PASSWORD)).status_code == 200

    (row,) = await audit_rows("auth.link_redeemed")
    assert row.payload == {"purpose": "invite", "sessions_revoked": 0}
    assert row.target_id == user.id
    assert_no_content(row.payload)


async def test_reset_link_revokes_all_sessions_and_unlocks(auth_env: AuthEnv) -> None:
    user = await create_user("rahul")
    async with make_client() as one, make_client() as two:
        await login(one, "rahul")
        await login(two, "rahul")
        async with transaction() as tx:
            await repository.set_locked_until(tx, user.id, utcnow() + timedelta(minutes=10))
        issued = await issue(user.id)
        assert issued.purpose is TokenPurpose.RESET

        assert (await redeem(issued.token, "rahul"))[0] == 204
        assert (await one.get("/api/v1/auth/me")).status_code == 401
        assert (await two.get("/api/v1/auth/me")).status_code == 401
    stored = await get_user(user.id)
    assert stored.locked_until is None
    assert stored.failed_login_count == 0
    (row,) = await audit_rows("auth.link_redeemed")
    assert row.payload["sessions_revoked"] == 2


@pytest.mark.parametrize("case", ["wrong_user", "expired", "reused", "unknown", "deactivated"])
async def test_every_link_failure_is_the_same_400(auth_env: AuthEnv, case: str) -> None:
    user = await create_user("mira", status=UserStatus.INVITED)
    await create_user("other")
    issued = await issue(user.id)
    token, username = issued.token, "mira"
    if case == "wrong_user":
        username = "other"
    elif case == "expired":
        token = (await issue(user.id, now=utcnow() - timedelta(days=2))).token
    elif case == "reused":
        assert (await redeem(token, username))[0] == 204
    elif case == "unknown":
        token = "x" * 43
    elif case == "deactivated":
        async with transaction() as tx:
            await repository.set_password_hash(tx, user.id, "x")  # only invited may lack one
            await repository.update_user_status(tx, user.id, UserStatus.DEACTIVATED)

    status, body = await redeem(token, username, NEW_PASSWORD + "!")
    assert status == 400
    assert body["code"] == "link_invalid"
    assert body["title"] == "This link is invalid or expired"


async def test_reissue_expires_the_earlier_link(auth_env: AuthEnv) -> None:
    user = await create_user("ines", status=UserStatus.INVITED)
    first = await issue(user.id)
    second = await issue(user.id)
    assert (await redeem(first.token, "ines"))[0] == 400
    assert (await redeem(second.token, "ines"))[0] == 204


async def test_redeem_expires_other_links(auth_env: AuthEnv) -> None:
    user = await create_user("omar", status=UserStatus.INVITED)
    issued = await issue(user.id)
    async with transaction() as tx:  # a second valid token, as if issued concurrently
        await repository.create_password_token(
            tx,
            user_id=user.id,
            token_hash=hash_token("second-token"),
            purpose=TokenPurpose.INVITE,
            expires_at=utcnow() + timedelta(hours=1),
        )
    assert (await redeem(issued.token, "omar"))[0] == 204
    assert (await redeem("second-token", "omar"))[0] == 400


async def test_policy_rejection_is_422_and_keeps_the_link(auth_env: AuthEnv) -> None:
    user = await create_user("kofi", status=UserStatus.INVITED)
    issued = await issue(user.id)
    status, body = await redeem(issued.token, "kofi", "xk3#Qv")
    assert status == 422
    assert body["code"] == "password_rejected"
    assert body["reasons"] == ["too_short"]
    status, body = await redeem(issued.token, "kofi", "password")
    assert set(body["reasons"]) == {"too_short", "breached"}
    status, body = await redeem(issued.token, "kofi", "kofi-and-a-long-tail")
    assert body["reasons"] == ["contains_username"]
    assert (await redeem(issued.token, "kofi"))[0] == 204


async def test_concurrent_double_redeem_succeeds_once(auth_env: AuthEnv) -> None:
    user = await create_user("zara", status=UserStatus.INVITED)
    issued = await issue(user.id)
    results = await asyncio.gather(*(redeem(issued.token, "zara") for _ in range(4)))
    assert sorted(status for status, _ in results) == [204, 400, 400, 400]


async def test_raw_token_is_never_stored_logged_or_audited(
    auth_env: AuthEnv, logs: LogCapture
) -> None:
    user = await create_user("tomas", status=UserStatus.INVITED)
    issued = await issue(user.id)
    await redeem(issued.token, "tomas", "short")
    await redeem(issued.token, "tomas")
    async with transaction() as tx:
        stored = (await tx.execute(select(PasswordToken.token_hash))).scalars().all()
    assert stored == [hash_token(issued.token)]
    assert issued.token.encode() not in b"".join(stored)
    for row in await audit_rows():
        assert issued.token not in str(row.payload)
    assert issued.token not in logs.text
    assert NEW_PASSWORD not in logs.text


async def test_set_password_needs_no_csrf_token_but_checks_origin(auth_env: AuthEnv) -> None:
    async with make_client(headers={"Origin": "https://evil.test"}) as client:
        response = await client.post(
            SET_PASSWORD, json={"token": "t", "username": "u", "new_password": NEW_PASSWORD}
        )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"
    assert (await redeem("t", "u"))[0] == 400  # no CSRF cookie or header: still reaches the route


async def test_signed_in_user_keeps_no_session_after_redeem(auth_env: AuthEnv) -> None:
    await create_user("ella")
    async with make_client() as client:
        await login(client, "ella", PASSWORD)
        assert session_cookie(client)
        user = await create_user("ella2", status=UserStatus.INVITED)
        issued = await issue(user.id)
        assert (await redeem(issued.token, "ella2"))[0] == 204
        # Only ella2's sessions are revoked.
        assert (await client.get("/api/v1/auth/me")).status_code == 200
