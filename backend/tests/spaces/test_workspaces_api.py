"""Story 8.3: deal workspaces API (create, rename, members, close, audit)."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.audit.testing import assert_no_content
from app.auth import repository
from app.auth.models import Role, UserStatus
from app.core.db import transaction
from app.jobs import registry as job_registry
from app.jobs.models import Job
from app.spaces import service_workspaces
from app.spaces.deps import load_space_ids
from tests.auth.conftest import AuthEnv, audit_rows, csrf_headers, login, make_client
from tests.conftest import PgDatabase
from tests.spaces.conftest import as_migrator, as_user, make_user, make_workspace

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("spaces_db")]

SPACES = "/api/v1/spaces"


@asynccontextmanager
async def signed_in(username: str) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client() as client:
        assert (await login(client, username)).status_code == 200
        client.headers.update(csrf_headers(client))
        yield client


async def create(client: httpx.AsyncClient, code_name: str) -> httpx.Response:
    return await client.post(SPACES, json={"code_name": code_name})


async def add(
    client: httpx.AsyncClient, space_id: object, username: str, role: str = "member"
) -> httpx.Response:
    return await client.post(
        f"{SPACES}/{space_id}/members", json={"username": username, "role": role}
    )


# --- AC #1 ------------------------------------------------------------------------------


async def test_create_workspace(auth_env: AuthEnv, pg: PgDatabase) -> None:
    lead = await make_user("lead")
    async with signed_in("lead") as client:
        response = await create(client, "  Project Falcon  ")
        assert response.status_code == 201
        body = response.json()
        assert body["kind"] == "workspace"
        assert body["my_role"] == "owner"
        assert body["code_name"] == "Project Falcon"
        listed = (await client.get(SPACES)).json()["items"]
    assert [s["code_name"] for s in listed if s["kind"] == "workspace"] == ["Project Falcon"]

    space_id = body["id"]
    rows = await as_migrator(
        pg.migrator_url,
        "SELECT s.code_name_enc, k.space_id FROM spaces s "
        "JOIN space_keys k ON k.space_id = s.id WHERE s.id = :id",
        {"id": space_id},
    )
    ((code_name_enc, _),) = rows
    assert code_name_enc is not None
    assert b"Falcon" not in bytes(code_name_enc)
    dump = await as_migrator(pg.migrator_url, "SELECT s::text FROM spaces s")
    assert all("Falcon" not in row[0] for row in dump)
    (row,) = await audit_rows("spaces.workspace_created")
    assert row.payload == {"space_id": space_id}
    assert row.actor_user_id == lead.id


@pytest.mark.parametrize("code_name", ["", "   ", "x" * 81, "bad\x00name", "tab\tname"])
async def test_invalid_code_names(auth_env: AuthEnv, code_name: str) -> None:
    await make_user("lead")
    async with signed_in("lead") as client:
        response = await create(client, code_name)
    assert response.status_code == 422


async def test_eighty_characters_and_unicode_are_fine(auth_env: AuthEnv) -> None:
    await make_user("lead")
    async with signed_in("lead") as client:
        assert (await create(client, "x" * 80)).status_code == 201
        response = await create(client, "Projekt Fälke — 鷹")
        assert response.json()["code_name"] == "Projekt Fälke — 鷹"


@pytest.mark.parametrize("role", [Role.ADMIN, Role.COMPLIANCE])
async def test_only_role_user_creates(auth_env: AuthEnv, role: Role) -> None:
    await make_user("staff", role=role)
    async with signed_in("staff") as client:
        assert (await create(client, "Falcon")).status_code == 403


# --- AC #2 ------------------------------------------------------------------------------


async def test_rename_owner_only_and_hidden_from_outsiders(auth_env: AuthEnv) -> None:
    owner = await make_user("owner")
    member = await make_user("member")
    await make_user("outsider")
    space_id = await make_workspace("Project Falcon", owner, member)

    async with signed_in("owner") as client:
        response = await client.patch(f"{SPACES}/{space_id}", json={"code_name": "Project Osprey"})
        assert response.status_code == 200
        assert response.json()["code_name"] == "Project Osprey"
    async with signed_in("member") as client:
        listed = (await client.get(SPACES)).json()["items"]
        assert "Project Osprey" in [s["code_name"] for s in listed]
        response = await client.patch(f"{SPACES}/{space_id}", json={"code_name": "Mine"})
        assert response.status_code == 403
        assert response.json()["code"] == "not_space_owner"
    async with signed_in("outsider") as client:
        responses = [
            await client.patch(f"{SPACES}/{space_id}", json={"code_name": "Mine"}),
            await client.get(f"{SPACES}/{space_id}/members"),
            await add(client, space_id, "outsider"),
            await client.request("DELETE", f"{SPACES}/{space_id}", json={"confirm": True}),
        ]
        listed = await client.get(SPACES)
    assert [r.status_code for r in responses] == [404] * 4
    assert all(r.json()["code"] == "space_not_found" for r in responses)
    for text in [listed.text, *(r.text for r in responses)]:
        assert "Osprey" not in text
        assert "Falcon" not in text
    (renamed,) = await audit_rows("spaces.workspace_renamed")
    assert renamed.payload == {"space_id": str(space_id)}


async def test_unknown_space_is_404(auth_env: AuthEnv) -> None:
    await make_user("lead")
    async with signed_in("lead") as client:
        response = await client.patch(f"{SPACES}/{uuid.uuid4()}", json={"code_name": "X"})
    assert response.status_code == 404


# --- AC #3 ------------------------------------------------------------------------------


async def test_members_add_list_and_roles(auth_env: AuthEnv) -> None:
    owner = await make_user("owner")
    peer = await make_user("peer")
    space_id = await make_workspace("Falcon", owner)
    async with signed_in("owner") as client:
        added = await add(client, space_id, "Peer")  # usernames are case-insensitive
        assert added.status_code == 201
        assert added.json()["role"] == "member"
        assert added.json()["user_id"] == str(peer.id)
        promoted = await add(client, space_id, "peer", "owner")
        assert promoted.status_code == 200
        assert promoted.json()["role"] == "owner"
        members = (await client.get(f"{SPACES}/{space_id}/members")).json()
    assert members["next_cursor"] is None
    assert [(m["username"], m["role"]) for m in members["items"]] == [
        ("owner", "owner"),
        ("peer", "owner"),
    ]
    async with signed_in("peer") as client:
        assert (await client.get(f"{SPACES}/{space_id}/members")).status_code == 200
    (row,) = await audit_rows("spaces.member_added")
    assert row.payload == {"space_id": str(space_id), "user_id": str(peer.id), "role": "member"}
    (row,) = await audit_rows("spaces.member_role_changed")
    assert row.payload["role"] == "owner"


@pytest.mark.parametrize("case", ["admin", "compliance", "invited", "deactivated", "unknown"])
async def test_only_active_role_user_can_be_added(auth_env: AuthEnv, case: str) -> None:
    owner = await make_user("owner")
    if case in {"admin", "compliance"}:
        await make_user("target", role=Role(case))
    elif case in {"invited", "deactivated"}:
        target = await make_user("target")
        async with transaction() as tx:
            await repository.update_user_status(tx, target.id, UserStatus(case))
    space_id = await make_workspace("Falcon", owner)
    async with signed_in("owner") as client:
        response = await add(client, space_id, "target")
    assert response.status_code == 404
    assert response.json()["code"] == "user_not_found"


async def test_member_cannot_manage_members(auth_env: AuthEnv) -> None:
    owner = await make_user("owner")
    member = await make_user("member")
    await make_user("third")
    space_id = await make_workspace("Falcon", owner, member)
    async with signed_in("member") as client:
        assert (await add(client, space_id, "third")).status_code == 403
        response = await client.delete(f"{SPACES}/{space_id}/members/{owner.id}")
        assert response.status_code == 403


async def test_last_owner_cannot_be_removed_or_demoted(auth_env: AuthEnv) -> None:
    owner = await make_user("owner")
    member = await make_user("member")
    space_id = await make_workspace("Falcon", owner, member)
    async with signed_in("owner") as client:
        removed = await client.delete(f"{SPACES}/{space_id}/members/{owner.id}")
        demoted = await add(client, space_id, "owner", "member")
    assert (removed.status_code, removed.json()["code"]) == (409, "last_owner")
    assert (demoted.status_code, demoted.json()["code"]) == (409, "last_owner")


async def test_owner_demotes_self_while_another_owner_exists(auth_env: AuthEnv) -> None:
    owner = await make_user("owner")
    await make_user("co")
    space_id = await make_workspace("Falcon", owner)
    async with signed_in("owner") as client:
        await add(client, space_id, "co", "owner")
        response = await add(client, space_id, "owner", "member")
        assert response.status_code == 200
        assert response.json()["role"] == "member"
        # Now only a member: owner-only actions are refused.
        assert (
            await client.patch(f"{SPACES}/{space_id}", json={"code_name": "X"})
        ).status_code == 403


async def test_removed_member_gets_404_next_request(auth_env: AuthEnv) -> None:
    owner = await make_user("owner")
    member = await make_user("member")
    space_id = await make_workspace("Falcon", owner, member)
    async with signed_in("member") as member_client, signed_in("owner") as owner_client:
        assert (await member_client.get(f"{SPACES}/{space_id}/members")).status_code == 200
        response = await owner_client.delete(f"{SPACES}/{space_id}/members/{member.id}")
        assert response.status_code == 204
        assert (await member_client.get(f"{SPACES}/{space_id}/members")).status_code == 404
        assert "Falcon" not in (await member_client.get(SPACES)).text
        # Re-adding works.
        assert (await add(owner_client, space_id, "member")).status_code == 201
        assert (await member_client.get(f"{SPACES}/{space_id}/members")).status_code == 200
    (row,) = await audit_rows("spaces.member_removed")
    assert row.payload == {"space_id": str(space_id), "user_id": str(member.id), "by_self": False}


async def test_member_can_leave(auth_env: AuthEnv) -> None:
    owner = await make_user("owner")
    member = await make_user("member")
    space_id = await make_workspace("Falcon", owner, member)
    async with signed_in("member") as client:
        response = await client.delete(f"{SPACES}/{space_id}/members/{member.id}")
        assert response.status_code == 204
    assert space_id not in await load_space_ids(member.id)
    (row,) = await audit_rows("spaces.member_removed")
    assert row.payload["by_self"] is True


async def test_concurrent_last_owner_leaves_serialise(auth_env: AuthEnv) -> None:
    a = await make_user("anna")
    b = await make_user("ben")
    space_id = await make_workspace("Falcon", a)
    async with signed_in("anna") as client:
        await add(client, space_id, "ben", "owner")
    async with signed_in("anna") as ca, signed_in("ben") as cb:
        results = await asyncio.gather(
            ca.delete(f"{SPACES}/{space_id}/members/{a.id}"),
            cb.delete(f"{SPACES}/{space_id}/members/{b.id}"),
        )
    assert sorted(r.status_code for r in results) == [204, 409]
    stayed = b if results[0].status_code == 204 else a
    owners = await as_user(
        stayed.id,
        "SELECT user_id FROM space_members WHERE space_id = :s AND role = 'owner'",
        s=space_id,
    )
    assert owners == [(stayed.id,)]


async def test_concurrent_cross_removal_leaves_one_owner(auth_env: AuthEnv) -> None:
    a = await make_user("anna")
    b = await make_user("ben")
    space_id = await make_workspace("Falcon", a)
    async with signed_in("anna") as client:
        await add(client, space_id, "ben", "owner")
    async with signed_in("anna") as ca, signed_in("ben") as cb:
        results = await asyncio.gather(
            ca.delete(f"{SPACES}/{space_id}/members/{b.id}"),
            cb.delete(f"{SPACES}/{space_id}/members/{a.id}"),
        )
    # The second runs after the first: its caller is no longer a member, so 404.
    assert sorted(r.status_code for r in results) == [204, 404]


async def test_private_space_has_no_members_to_manage(auth_env: AuthEnv) -> None:
    await make_user("solo")
    await make_user("other")
    async with signed_in("solo") as client:
        private = (await client.get(SPACES)).json()["items"][0]
        assert private["kind"] == "private"
        response = await add(client, private["id"], "other")
    assert (response.status_code, response.json()["code"]) == (409, "private_space")


# --- AC #4 ------------------------------------------------------------------------------


async def test_close_hides_everything_at_once(auth_env: AuthEnv) -> None:
    owner = await make_user("owner")
    member = await make_user("member")
    space_id = await make_workspace("Falcon", owner, member)
    async with signed_in("owner") as client, signed_in("member") as member_client:
        unconfirmed = await client.request(
            "DELETE", f"{SPACES}/{space_id}", json={"confirm": False}
        )
        assert unconfirmed.status_code == 422
        missing = await client.request("DELETE", f"{SPACES}/{space_id}")
        assert missing.status_code == 422
        response = await client.request("DELETE", f"{SPACES}/{space_id}", json={"confirm": True})
        assert response.status_code == 204
        for c in (client, member_client):
            assert "Falcon" not in (await c.get(SPACES)).text
            assert (await c.get(f"{SPACES}/{space_id}/members")).status_code == 404
    assert await as_user(owner.id, "SELECT id FROM spaces WHERE id = :s", s=space_id) == []
    assert space_id not in await load_space_ids(member.id)
    (row,) = await audit_rows("spaces.workspace_closed")
    assert row.payload == {"space_id": str(space_id), "deletion_queued": False}


async def test_close_queues_deletion_when_the_pipeline_is_registered(auth_env: AuthEnv) -> None:
    class Payload(BaseModel):
        space_id: uuid.UUID

    async def handler(*_: object) -> None:
        return None

    job_registry.register(service_workspaces.DELETE_SPACE_JOB, Payload, handler)
    try:
        owner = await make_user("owner")
        space_id = await make_workspace("Falcon", owner)
        async with signed_in("owner") as client:
            response = await client.request(
                "DELETE", f"{SPACES}/{space_id}", json={"confirm": True}
            )
        assert response.status_code == 204
    finally:
        job_registry.unregister(service_workspaces.DELETE_SPACE_JOB)
    async with transaction() as tx:
        jobs = (await tx.execute(select(Job))).scalars().all()
    assert [(j.type, j.space_id, j.payload) for j in jobs] == [
        (service_workspaces.DELETE_SPACE_JOB, space_id, {"space_id": str(space_id)})
    ]
    (row,) = await audit_rows("spaces.workspace_closed")
    assert row.payload["deletion_queued"] is True


async def test_private_space_cannot_be_closed(auth_env: AuthEnv) -> None:
    await make_user("solo")
    async with signed_in("solo") as client:
        private = (await client.get(SPACES)).json()["items"][0]
        response = await client.request(
            "DELETE", f"{SPACES}/{private['id']}", json={"confirm": True}
        )
    assert (response.status_code, response.json()["code"]) == (409, "private_space")


async def test_member_cannot_close(auth_env: AuthEnv) -> None:
    owner = await make_user("owner")
    member = await make_user("member")
    space_id = await make_workspace("Falcon", owner, member)
    async with signed_in("member") as client:
        response = await client.request("DELETE", f"{SPACES}/{space_id}", json={"confirm": True})
    assert response.status_code == 403


# --- AC #5 ------------------------------------------------------------------------------


async def test_every_workspace_event_is_metadata_only(auth_env: AuthEnv) -> None:
    await make_user("owner")
    await make_user("peer")
    async with signed_in("owner") as client:
        space_id = (await create(client, "Planted Codename 4471")).json()["id"]
        await client.patch(f"{SPACES}/{space_id}", json={"code_name": "Planted Rename 9902"})
        await add(client, space_id, "peer")
        await add(client, space_id, "peer", "owner")
        peer_id = (await client.get(f"{SPACES}/{space_id}/members")).json()["items"][1]["user_id"]
        await client.delete(f"{SPACES}/{space_id}/members/{peer_id}")
        await client.request("DELETE", f"{SPACES}/{space_id}", json={"confirm": True})
    rows = await audit_rows()
    assert {r.event_type for r in rows} >= {
        "spaces.workspace_created",
        "spaces.workspace_renamed",
        "spaces.member_added",
        "spaces.member_role_changed",
        "spaces.member_removed",
        "spaces.workspace_closed",
    }
    for row in rows:
        assert_no_content(row.payload)
        assert "Planted" not in str(row.payload)
