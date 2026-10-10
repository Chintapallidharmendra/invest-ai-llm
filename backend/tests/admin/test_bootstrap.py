"""AC #6: ``python -m app.admin.bootstrap`` creates the first admin only."""

import asyncio

import pytest

from app.admin import bootstrap
from app.auth import repository
from app.auth.models import Role, UserStatus
from app.core.db import transaction
from tests.admin.conftest import token_of
from tests.auth.conftest import AuthEnv, audit_rows, login, make_client
from tests.core.conftest import LogCapture, logs

__all__ = ["logs"]

pytestmark = pytest.mark.db

NEW_PASSWORD = "first admin passphrase 2026"  # noqa: S105  gitleaks:allow (test value)


async def test_first_admin_then_refusal(
    admin_env: AuthEnv, logs: LogCapture, capsys: pytest.CaptureFixture[str]
) -> None:
    # `logs` before `capsys`: its teardown re-points logging at sys.stdout, which must
    # not be capsys's buffer by then (it is closed after the test)."""
    code = await bootstrap.run("boss", "b@x.test")
    assert code == 0
    out = capsys.readouterr()
    url = out.out.strip()
    token = token_of(url)
    assert out.err == ""
    assert token not in logs.text

    async with transaction() as tx:
        user = await repository.get_user_by_username(tx, "boss")
    assert user is not None
    assert (user.role, user.status) == (Role.ADMIN, UserStatus.INVITED)
    (created,) = await audit_rows("admin.user_created")
    assert created.payload == {"role": "admin", "bootstrap": True}
    assert created.actor_user_id is None

    async with make_client() as client:
        response = await client.post(
            "/api/v1/auth/set-password",
            json={"token": token, "username": "boss", "new_password": NEW_PASSWORD},
        )
        assert response.status_code == 204
        assert (await login(client, "boss", NEW_PASSWORD)).status_code == 200

    code = await bootstrap.run("boss2", "c@x.test")
    assert code == bootstrap.EXIT_ADMIN_EXISTS
    out = capsys.readouterr()
    assert out.out == ""
    assert "already exists" in out.err


async def test_concurrent_bootstraps_create_one_admin(admin_env: AuthEnv) -> None:
    results = await asyncio.gather(
        *(bootstrap.bootstrap(f"boss{i}", f"b{i}@x.test") for i in range(3)),
        return_exceptions=True,
    )
    created = [r for r in results if isinstance(r, str)]
    assert len(created) == 1
    assert all(isinstance(r, bootstrap.AdminExistsError) for r in results if r not in created)


def test_invalid_arguments_create_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    assert bootstrap.main(["--username", "bad name", "--email", "nope"]) == 2
    assert capsys.readouterr().err.strip() == "Invalid email, username"
