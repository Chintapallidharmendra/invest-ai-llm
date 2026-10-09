"""AC #5: GET /api/v1/spaces."""

import pytest
from sqlalchemy import text

from app.api.app import create_app
from app.core.db import transaction
from app.spaces.routes import discover_subrouters
from tests.auth.conftest import AuthEnv, login, make_client
from tests.spaces.conftest import make_user, make_workspace

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("spaces_db")]


async def test_lists_own_private_space_and_workspaces(auth_env: AuthEnv) -> None:
    a = await make_user("anika")
    b = await make_user("bilal")
    falcon = await make_workspace("Project Falcon", a, b)
    kestrel = await make_workspace("Project Kestrel", b)
    await make_workspace("Project Closed", a, closed=True)

    async with make_client() as client:
        await login(client, "anika")
        response = await client.get("/api/v1/spaces")
    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] is None
    items = body["items"]
    assert [i["kind"] for i in items] == ["private", "workspace"]
    private, workspace = items
    assert private["my_role"] == "owner"
    assert private["code_name"] is None
    assert workspace == {
        "id": str(falcon),
        "kind": "workspace",
        "my_role": "owner",
        "code_name": "Project Falcon",
    }
    assert str(kestrel) not in response.text
    assert "Kestrel" not in response.text
    assert "Closed" not in response.text

    async with make_client() as client:
        await login(client, "bilal")
        items = (await client.get("/api/v1/spaces")).json()["items"]
    roles = {i["code_name"]: i["my_role"] for i in items if i["kind"] == "workspace"}
    assert roles == {"Project Falcon": "member", "Project Kestrel": "owner"}


async def test_requires_a_session(auth_env: AuthEnv) -> None:
    async with make_client() as client:
        response = await client.get("/api/v1/spaces")
    assert response.status_code == 401


async def test_removed_member_stops_seeing_the_workspace_at_once(auth_env: AuthEnv) -> None:
    owner = await make_user("olu")
    member = await make_user("mei")
    workspace = await make_workspace("Project Heron", owner, member)
    async with make_client() as client:
        await login(client, "mei")
        assert "Heron" in (await client.get("/api/v1/spaces")).text
        async with transaction(context={"app.user_id": str(owner.id)}) as tx:
            await tx.execute(
                text("DELETE FROM space_members WHERE space_id = :s AND user_id = :u"),
                {"s": workspace, "u": member.id},
            )
        response = await client.get("/api/v1/spaces")
    assert "Heron" not in response.text
    assert len(response.json()["items"]) == 1


def test_routes_mount_subrouters() -> None:
    assert all(name.startswith("app.spaces.routes_") for name in discover_subrouters())


def test_spaces_path_is_in_openapi() -> None:
    paths = create_app(configure_logs=False, readiness_checks=False).openapi()["paths"]
    assert "/api/v1/spaces" in paths
