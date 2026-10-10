"""AC #1 (create), AC #2 (links), AC #5 (roles and audit)."""

import httpx
import pytest
from sqlalchemy import ColumnElement, func, select

from app.admin import service_users
from app.audit.testing import assert_no_content
from app.auth.models import PasswordToken, Role, User
from app.core import events
from app.core.db import transaction
from app.crypto.models import SpaceKey
from app.spaces.models import Space, SpaceKind, SpaceMember
from tests.admin.conftest import signed_in, token_of
from tests.auth.conftest import audit_rows, login, make_client
from tests.conftest import PgDatabase
from tests.core.conftest import LogCapture, logs
from tests.spaces.conftest import as_migrator, make_user

__all__ = ["logs"]

pytestmark = pytest.mark.db

USERS = "/api/v1/admin/users"
UNKNOWN_ID = "01900000-0000-7000-8000-000000000000"
NEW_PASSWORD = "a brand new passphrase 2026"  # noqa: S105  gitleaks:allow (test value)


async def create(client: httpx.AsyncClient, username: str, **body: str) -> httpx.Response:
    payload = {"username": username, "email": f"{username}@example.test", "role": "user", **body}
    return await client.post(USERS, json=payload)


async def count(model: type, *where: ColumnElement[bool]) -> int:
    async with transaction() as tx:
        stmt = select(func.count()).select_from(model).where(*where)
        return (await tx.execute(stmt)).scalar_one()


async def test_create_returns_invited_user_and_show_once_link(
    admin_client: httpx.AsyncClient, admin: User, pg: PgDatabase
) -> None:
    response = await create(admin_client, "priya")
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    user = body["user"]
    assert {user["username"], user["role"], user["status"]} == {"priya", "user", "invited"}
    assert user["last_login_at"] is None
    assert body["link"]["purpose"] == "invite"
    token = token_of(body["link"]["url"])
    assert len(token) == 43

    # A private space, its owner membership and its key exist (8.2's subscriber).
    rows = await as_migrator(
        pg.migrator_url,
        "SELECT s.id FROM spaces s JOIN space_members m ON m.space_id = s.id "
        "JOIN space_keys k ON k.space_id = s.id "
        "WHERE s.kind = 'private' AND s.owner_user_id = :u AND m.user_id = :u",
        {"u": user["id"]},
    )
    assert len(rows) == 1

    # The link works: set a password, then sign in.
    async with make_client() as client:
        redeemed = await client.post(
            "/api/v1/auth/set-password",
            json={"token": token, "username": "priya", "new_password": NEW_PASSWORD},
        )
        assert redeemed.status_code == 204
        assert (await login(client, "priya", NEW_PASSWORD)).status_code == 200

    created = await audit_rows("admin.user_created")
    assert [(r.actor_user_id, str(r.target_id)) for r in created] == [(admin.id, user["id"])]
    assert created[0].payload == {"role": "user", "bootstrap": False}
    (issued,) = await audit_rows("admin.link_issued")
    assert issued.payload["purpose"] == "invite"


@pytest.mark.parametrize(
    ("username", "email", "field"),
    [
        ("Priya", "other@example.test", "username"),  # citext: case-insensitive
        ("someone", "PRIYA@example.test", "email"),
    ],
)
async def test_duplicates_are_409_naming_the_field(
    admin_client: httpx.AsyncClient, username: str, email: str, field: str
) -> None:
    assert (await create(admin_client, "priya")).status_code == 201
    response = await create(admin_client, username, email=email)
    assert response.status_code == 409
    assert response.json()["code"] == "user_exists"
    assert response.json()["field"] == field
    assert await count(User) == 2  # the admin and priya


async def test_failure_after_insert_leaves_no_user_and_no_space(
    admin: User, pg: PgDatabase
) -> None:
    async def boom(**_: object) -> None:
        raise RuntimeError("forced")

    users_before = await count(User)
    spaces_before = await as_migrator(pg.migrator_url, "SELECT count(*) FROM spaces")
    keys_before = await count(SpaceKey)
    unsubscribe = events.subscribe(service_users.USER_CREATED_EVENT, boom)
    try:
        with pytest.raises(RuntimeError, match="forced"):
            await service_users.create_user(
                admin.id, username="ghost", email="ghost@example.test", role=Role.USER
            )
    finally:
        unsubscribe()
    assert await count(User) == users_before
    assert await as_migrator(pg.migrator_url, "SELECT count(*) FROM spaces") == spaces_before
    assert await count(SpaceKey) == keys_before
    assert await count(PasswordToken) == 0


@pytest.mark.parametrize(
    "body",
    [
        {"username": "has space", "email": "a@example.test", "role": "user"},
        {"username": "", "email": "a@example.test", "role": "user"},
        {"username": "ok", "email": "not-an-email", "role": "user"},
        {"username": "ok", "email": "a@example.test", "role": "superuser"},
        {"username": "ok", "email": "a@example.test", "role": "user", "extra": 1},
    ],
)
async def test_invalid_bodies_are_422(
    admin_client: httpx.AsyncClient, body: dict[str, object]
) -> None:
    response = await admin_client.post(USERS, json=body)
    assert response.status_code == 422


async def test_creates_admin_and_compliance_users(admin_client: httpx.AsyncClient) -> None:
    for role in ("admin", "compliance"):
        response = await create(admin_client, f"new-{role}", role=role)
        assert response.status_code == 201
        assert response.json()["user"]["role"] == role


async def test_reissue_expires_earlier_links(admin_client: httpx.AsyncClient) -> None:
    created = (await create(admin_client, "lena")).json()
    first = token_of(created["link"]["url"])
    response = await admin_client.post(f"{USERS}/{created['user']['id']}/set-password-links")
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["purpose"] == "invite"
    second = token_of(response.json()["url"])
    assert second != first

    async with make_client() as client:
        for token, expected in [(first, 400), (second, 204)]:
            result = await client.post(
                "/api/v1/auth/set-password",
                json={"token": token, "username": "lena", "new_password": NEW_PASSWORD},
            )
            assert result.status_code == expected

    # Once active, a re-issued link is a reset.
    response = await admin_client.post(f"{USERS}/{created['user']['id']}/set-password-links")
    assert response.json()["purpose"] == "reset"
    assert [r.payload["purpose"] for r in await audit_rows("admin.link_issued")] == [
        "invite",
        "invite",
        "reset",
    ]


async def test_no_link_for_deactivated_or_unknown_users(admin_client: httpx.AsyncClient) -> None:
    created = (await create(admin_client, "dev")).json()
    user_id = created["user"]["id"]
    await admin_client.patch(f"{USERS}/{user_id}", json={"status": "deactivated"})
    response = await admin_client.post(f"{USERS}/{user_id}/set-password-links")
    assert response.status_code == 409
    assert response.json()["code"] == "user_deactivated"
    unknown = await admin_client.post(f"{USERS}/{UNKNOWN_ID}/set-password-links")
    assert unknown.status_code == 404


@pytest.mark.parametrize("role", [Role.USER, Role.COMPLIANCE])
async def test_non_admins_get_403_and_an_audit_row(admin: User, role: Role) -> None:
    caller = await make_user(f"not-admin-{role}", role=role)
    async with signed_in(caller.username) as client:
        responses = [
            await client.get(USERS),
            await create(client, "x"),
            await client.patch(f"{USERS}/{admin.id}", json={"role": "user"}),
            await client.post(f"{USERS}/{admin.id}/set-password-links"),
            await client.post(f"{USERS}/{admin.id}/session-revocations"),
        ]
    assert [r.status_code for r in responses] == [403] * 5
    denied = await audit_rows("auth.access_denied")
    assert len(denied) == 5
    assert {r.actor_user_id for r in denied} == {caller.id}
    assert await count(User) == 2


async def test_every_admin_event_is_metadata_only(
    admin_client: httpx.AsyncClient, logs: LogCapture
) -> None:
    created = (await create(admin_client, "planted.name.88")).json()
    user_id = created["user"]["id"]
    links = [created["link"]["url"]]
    links.append((await admin_client.post(f"{USERS}/{user_id}/set-password-links")).json()["url"])
    await admin_client.patch(f"{USERS}/{user_id}", json={"role": "compliance"})
    await admin_client.post(f"{USERS}/{user_id}/session-revocations")
    await admin_client.patch(f"{USERS}/{user_id}", json={"status": "deactivated"})

    rows = await audit_rows()
    assert {r.event_type for r in rows} >= {
        "admin.user_created",
        "admin.link_issued",
        "admin.user_updated",
        "admin.sessions_revoked",
    }
    for row in rows:
        assert_no_content(row.payload)
        for secret in ["planted.name.88", "planted.name.88@example.test", *map(token_of, links)]:
            assert secret not in str(row.payload)
    for link in links:
        assert token_of(link) not in logs.text
    assert "planted.name.88" not in logs.text


async def test_private_space_only_for_the_new_user(
    admin_client: httpx.AsyncClient, pg: PgDatabase
) -> None:
    created = (await create(admin_client, "sana")).json()
    rows = await as_migrator(
        pg.migrator_url,
        "SELECT kind::text, owner_user_id::text FROM spaces WHERE owner_user_id = :u",
        {"u": created["user"]["id"]},
    )
    assert rows == [(SpaceKind.PRIVATE.value, created["user"]["id"])]
    assert await count(Space) == 0  # app_rw without an RLS context sees no spaces
    assert await count(SpaceMember) == 0


async def test_invited_user_cannot_sign_in_before_setting_a_password(
    admin_client: httpx.AsyncClient,
) -> None:
    await create(admin_client, "yusuf")
    async with make_client() as client:
        assert (await login(client, "yusuf")).status_code == 401
