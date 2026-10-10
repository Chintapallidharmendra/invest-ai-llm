"""Story 2.4: change your own password."""

import asyncio
import unicodedata

import httpx
import pytest

from app.audit.testing import assert_no_content
from tests.auth.conftest import (
    PASSWORD,
    AuthEnv,
    audit_rows,
    create_user,
    csrf_headers,
    get_user,
    login,
    make_client,
)
from tests.core.conftest import LogCapture, logs

__all__ = ["logs"]

pytestmark = pytest.mark.db

CHANGE = "/api/v1/auth/change-password"
NEW_PASSWORD = "an entirely new passphrase 77"  # noqa: S105  gitleaks:allow (test value)


async def change(
    client: httpx.AsyncClient, current: str, new: str = NEW_PASSWORD
) -> httpx.Response:
    return await client.post(
        CHANGE,
        json={"current_password": current, "new_password": new},
        headers=csrf_headers(client),
    )


async def test_change_keeps_current_session_and_ends_others(auth_env: AuthEnv) -> None:
    user = await create_user("hira")
    async with make_client() as current, make_client() as other:
        await login(current, "hira")
        await login(other, "hira")
        response = await change(current, PASSWORD)
        assert response.status_code == 204
        assert (await current.get("/api/v1/auth/me")).status_code == 200
        assert (await other.get("/api/v1/auth/me")).status_code == 401
    async with make_client() as client:
        assert (await login(client, "hira", PASSWORD)).status_code == 401
        assert (await login(client, "hira", NEW_PASSWORD)).status_code == 200

    (row,) = await audit_rows("auth.password_changed")
    assert row.payload == {"sessions_revoked": 1}
    assert (row.actor_user_id, row.target_id) == (user.id, user.id)
    assert_no_content(row.payload)


async def test_wrong_current_password_counts_toward_lockout(auth_env: AuthEnv) -> None:
    user = await create_user("theo")
    async with make_client() as client:
        await login(client, "theo")
        for attempt in range(1, 6):
            response = await change(client, "not my password")
            assert response.status_code == 400
            assert response.json()["code"] == "current_password_invalid"
            assert (await get_user(user.id)).failed_login_count == attempt
        # Locked: even the right current password fails now, and login does too.
        assert (await change(client, PASSWORD)).status_code == 400
    async with make_client() as fresh:
        assert (await login(fresh, "theo")).status_code == 401
    stored = await get_user(user.id)
    assert stored.locked_until is not None
    assert stored.password_hash is not None
    failed = await audit_rows("auth.password_change_failed")
    assert [r.payload["failed_count"] for r in failed] == [1, 2, 3, 4, 5, None]
    (locked,) = await audit_rows("auth.account_locked")
    assert locked.payload["failed_count"] == 5


async def test_failures_mix_with_login_failures(auth_env: AuthEnv) -> None:
    user = await create_user("ugo")
    async with make_client() as client:
        await login(client, "ugo")
        for _ in range(3):
            await login(client, "ugo", "wrong")
        assert (await get_user(user.id)).failed_login_count == 3
        await change(client, "wrong")
        assert (await get_user(user.id)).failed_login_count == 4


@pytest.mark.parametrize(
    ("new", "reasons"),
    [
        ("xk3#Qv", ["too_short"]),
        ("password", ["too_short", "breached"]),
        ("my name is wren, really", ["contains_username"]),
        ("y" * 1025, ["too_long"]),
    ],
)
async def test_policy_rejections(auth_env: AuthEnv, new: str, reasons: list[str]) -> None:
    await create_user("wren")
    async with make_client() as client:
        await login(client, "wren")
        response = await change(client, PASSWORD, new)
    assert response.status_code == 422
    assert response.json()["code"] == "password_rejected"
    assert response.json()["reasons"] == reasons


@pytest.mark.parametrize("variant", ["same", "nfkc"])
async def test_reusing_the_current_password_is_unchanged(auth_env: AuthEnv, variant: str) -> None:
    # Fullwidth letters NFKC-normalise to ASCII ones.
    fullwidth = "".join(chr(0xFEE0 + ord(c)) for c in "fullwidth")
    current = f"{fullwidth} passphrase 12"
    await create_user("yuki", password=current)
    new = current if variant == "same" else unicodedata.normalize("NFKC", current)
    assert new != current or variant == "same"
    async with make_client() as client:
        await login(client, "yuki", current)
        response = await change(client, current, new)
    assert response.status_code == 422
    assert response.json()["code"] == "password_unchanged"


async def test_requires_session_and_csrf(auth_env: AuthEnv) -> None:
    await create_user("zed")
    async with make_client() as client:
        anonymous = await client.post(
            CHANGE, json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
        )
        assert anonymous.status_code == 403  # no CSRF token: refused before auth
        await login(client, "zed")
        no_token = await client.post(
            CHANGE, json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
        )
        assert no_token.status_code == 403
        assert no_token.json()["code"] == "csrf_failed"
    async with make_client() as client:
        response = await client.post(
            CHANGE,
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            headers={"X-CSRF-Token": "t", "Cookie": "__Host-csrf=t"},
        )
        assert response.status_code == 401


async def test_concurrent_changes_run_one_after_the_other(auth_env: AuthEnv) -> None:
    await create_user("ada")
    async with make_client() as client:
        await login(client, "ada")
        first, second = await asyncio.gather(
            change(client, PASSWORD, NEW_PASSWORD + " one"),
            change(client, PASSWORD, NEW_PASSWORD + " two"),
        )
    # The second is checked against the first's new password, so it fails.
    assert sorted([first.status_code, second.status_code]) == [204, 400]


async def test_no_password_in_logs_audit_or_responses(auth_env: AuthEnv, logs: LogCapture) -> None:
    planted_current = "Planted-Current-Secret-5521"
    planted_new = "Planted-New-Secret-Passphrase-8812"
    await create_user("pia", password=planted_current)
    texts = []
    async with make_client() as client:
        await login(client, "pia", planted_current)
        for current, new in [
            (planted_new, planted_new),  # wrong current
            (planted_current, "short"),  # rejected
            (planted_current, planted_current),  # unchanged
            (planted_current, planted_new),  # success
        ]:
            texts.append((await change(client, current, new)).text)
    rows = await audit_rows()
    for row in rows:
        assert_no_content(row.payload)
    for haystack in [*texts, logs.text, *(str(r.payload) for r in rows)]:
        assert planted_current not in haystack
        assert planted_new not in haystack
