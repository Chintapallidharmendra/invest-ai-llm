"""AC #4: list, update (role, status, unlock), guards and session revocations."""

import asyncio
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import text

from app.admin import service_users
from app.auth import repository
from app.auth.models import Role, User, UserStatus
from app.core.db import transaction
from app.core.errors import ProblemException
from app.core.timeutil import utcnow
from tests.admin.conftest import signed_in
from tests.auth.conftest import audit_rows, get_user, login, make_client
from tests.spaces.conftest import make_user, make_workspace

pytestmark = pytest.mark.db

USERS = "/api/v1/admin/users"
ADMIN_FIELDS = {
    "id",
    "username",
    "email",
    "role",
    "status",
    "locked",
    "last_login_at",
    "created_at",
}


async def test_list_shows_metadata_only(admin_client: httpx.AsyncClient, admin: User) -> None:
    await make_user("hana")
    response = await admin_client.get(USERS)
    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] is None
    items = body["items"]
    assert [i["username"] for i in items] == ["root", "hana"]
    assert all(set(i) == ADMIN_FIELDS for i in items)
    root = items[0]
    assert root["role"] == "admin"
    assert root["status"] == "active"
    assert root["last_login_at"] is not None  # the admin_client signed in
    assert root["locked"] is False


async def test_deactivation_ends_sessions_at_once(admin_client: httpx.AsyncClient) -> None:
    user = await make_user("dmitri")
    async with signed_in("dmitri") as client:
        assert (await client.get("/api/v1/auth/me")).status_code == 200
        response = await admin_client.patch(f"{USERS}/{user.id}", json={"status": "deactivated"})
        assert response.status_code == 200
        assert response.json()["user"]["status"] == "deactivated"
        assert (await client.get("/api/v1/auth/me")).status_code == 401
    async with make_client() as fresh:
        assert (await login(fresh, "dmitri")).status_code == 401
    (row,) = await audit_rows("admin.user_updated")
    assert row.payload == {
        "new_role": None,
        "new_status": "deactivated",
        "unlocked": False,
        "sessions_revoked": 1,
    }


async def test_deactivation_reports_space_counts(admin_client: httpx.AsyncClient) -> None:
    lead = await make_user("lead")
    peer = await make_user("peer")
    await make_workspace("Falcon", lead)  # lead is the only owner
    shared = await make_workspace("Heron", lead, peer)
    async with transaction(context={"app.user_id": str(lead.id)}) as tx:
        await tx.execute(
            text("UPDATE space_members SET role = 'owner' WHERE space_id = :s AND user_id = :u"),
            {"s": shared, "u": peer.id},
        )
    await make_workspace("Kite", peer, lead)  # lead is a member
    await make_workspace("Closed", lead, closed=True)

    response = await admin_client.patch(f"{USERS}/{lead.id}", json={"status": "deactivated"})
    assert response.json()["spaces"] == {
        "private_spaces": 1,
        "owned_workspaces": 2,
        "sole_owner_workspaces": 1,
        "member_workspaces": 1,
    }
    assert "Falcon" not in response.text


async def test_reactivation(admin_client: httpx.AsyncClient) -> None:
    user = await make_user("rosa")
    await admin_client.patch(f"{USERS}/{user.id}", json={"status": "deactivated"})
    response = await admin_client.patch(f"{USERS}/{user.id}", json={"status": "active"})
    assert response.json()["user"]["status"] == "active"
    async with make_client() as client:
        assert (await login(client, "rosa")).status_code == 200


async def test_deactivating_an_invited_user_and_reactivating(
    admin_client: httpx.AsyncClient,
) -> None:
    created = await admin_client.post(
        USERS, json={"username": "ivy", "email": "ivy@example.test", "role": "user"}
    )
    user_id = created.json()["user"]["id"]
    token = created.json()["link"]["url"].split("#t=")[1]
    response = await admin_client.patch(f"{USERS}/{user_id}", json={"status": "deactivated"})
    assert response.json()["user"]["status"] == "deactivated"
    # The invite link stopped working with the deactivation.
    async with make_client() as client:
        result = await client.post(
            "/api/v1/auth/set-password",
            json={"token": token, "username": "ivy", "new_password": "a long new passphrase"},
        )
    assert result.status_code == 400
    # Re-activated, they sign in through a reset link (no password was ever set).
    response = await admin_client.patch(f"{USERS}/{user_id}", json={"status": "active"})
    assert response.json()["user"]["status"] == "active"
    async with make_client() as client:
        assert (await login(client, "ivy", "a long new passphrase")).status_code == 401
    link = await admin_client.post(f"{USERS}/{user_id}/set-password-links")
    assert link.json()["purpose"] == "reset"


async def test_role_change_takes_effect_on_next_request(admin_client: httpx.AsyncClient) -> None:
    user = await make_user("ana")
    async with signed_in("ana") as client:
        assert (await client.get(USERS)).status_code == 403
        response = await admin_client.patch(f"{USERS}/{user.id}", json={"role": "admin"})
        assert response.json()["user"]["role"] == "admin"
        assert (await client.get(USERS)).status_code == 200
        assert (await client.get("/api/v1/auth/me")).json()["role"] == "admin"
    (row,) = await audit_rows("admin.user_updated")
    assert row.payload["new_role"] == "admin"


async def test_unlock_resets_the_counter(admin_client: httpx.AsyncClient) -> None:
    user = await make_user("luca")
    async with make_client() as client:
        for _ in range(5):
            await login(client, "luca", "wrong password, five times")
        assert (await login(client, "luca")).status_code == 401
    listed = {u["username"]: u for u in (await admin_client.get(USERS)).json()["items"]}
    assert listed["luca"]["locked"] is True

    response = await admin_client.patch(f"{USERS}/{user.id}", json={"unlock": True})
    assert response.json()["user"]["locked"] is False
    stored = await get_user(user.id)
    assert stored.failed_login_count == 0
    assert stored.locked_until is None
    async with make_client() as client:
        assert (await login(client, "luca")).status_code == 200
    (row,) = await audit_rows("admin.user_updated")
    assert row.payload["unlocked"] is True


async def test_unlocking_an_unlocked_user_changes_nothing(admin_client: httpx.AsyncClient) -> None:
    user = await make_user("noor")
    response = await admin_client.patch(f"{USERS}/{user.id}", json={"unlock": True})
    assert response.status_code == 200
    assert await audit_rows("admin.user_updated") == []


async def test_admin_cannot_demote_or_deactivate_self(
    admin_client: httpx.AsyncClient, admin: User
) -> None:
    await make_user("second-admin", role=Role.ADMIN)  # not the last admin
    for body in ({"role": "user"}, {"status": "deactivated"}):
        response = await admin_client.patch(f"{USERS}/{admin.id}", json=body)
        assert response.status_code == 409
        assert response.json()["code"] == "cannot_demote_self"
    assert (await get_user(admin.id)).role is Role.ADMIN


async def test_last_admin_cannot_be_removed(admin: User) -> None:
    other = await make_user("other-admin", role=Role.ADMIN)
    # `other` demotes `admin`: allowed, two admins remain... then one.
    await service_users.update_user(other.id, admin.id, role=Role.USER)
    # Now `other` is the last admin; a third admin can't demote them.
    invited = await make_user("invited-admin", role=Role.ADMIN)
    async with transaction() as tx:
        await repository.update_user_status(tx, invited.id, UserStatus.DEACTIVATED)
    with pytest.raises(ProblemException) as excinfo:
        await service_users.update_user(invited.id, other.id, status=UserStatus.DEACTIVATED)
    assert excinfo.value.code == "last_admin"


async def test_concurrent_cross_demotion_leaves_one_admin(admin: User) -> None:
    other = await make_user("rival", role=Role.ADMIN)
    results = await asyncio.gather(
        service_users.update_user(admin.id, other.id, role=Role.USER),
        service_users.update_user(other.id, admin.id, role=Role.USER),
        return_exceptions=True,
    )
    errors = [r for r in results if isinstance(r, ProblemException)]
    assert len(errors) == 1
    assert errors[0].code == "last_admin"
    roles = {(await get_user(u.id)).role for u in (admin, other)}
    assert roles == {Role.ADMIN, Role.USER}


async def test_session_revocations(admin_client: httpx.AsyncClient) -> None:
    user = await make_user("sven")
    async with signed_in("sven") as one, signed_in("sven") as two:
        response = await admin_client.post(f"{USERS}/{user.id}/session-revocations")
        assert response.status_code == 200
        assert response.json() == {"revoked": 2}
        assert (await one.get("/api/v1/auth/me")).status_code == 401
        assert (await two.get("/api/v1/auth/me")).status_code == 401
    (row,) = await audit_rows("admin.sessions_revoked")
    assert row.payload == {"count": 2}
    assert row.target_id == user.id


@pytest.mark.parametrize(
    "body", [{}, {"status": "locked"}, {"status": "invited"}, {"role": "root"}, {"unlock": False}]
)
async def test_invalid_updates_are_422(
    admin_client: httpx.AsyncClient, body: dict[str, object]
) -> None:
    user = await make_user("val")
    assert (await admin_client.patch(f"{USERS}/{user.id}", json=body)).status_code == 422


async def test_unknown_user_is_404(admin_client: httpx.AsyncClient) -> None:
    unknown = "01900000-0000-7000-8000-000000000000"
    response = await admin_client.patch(f"{USERS}/{unknown}", json={"unlock": True})
    assert response.status_code == 404
    response = await admin_client.post(f"{USERS}/{unknown}/session-revocations")
    assert response.status_code == 404
    assert response.json()["code"] == "user_not_found"


async def test_lock_expiry_shows_unlocked(admin_client: httpx.AsyncClient) -> None:
    user = await make_user("eve")
    async with transaction() as tx:
        await repository.set_locked_until(tx, user.id, utcnow() - timedelta(minutes=1))
    listed = {u["username"]: u for u in (await admin_client.get(USERS)).json()["items"]}
    assert listed["eve"]["locked"] is False
