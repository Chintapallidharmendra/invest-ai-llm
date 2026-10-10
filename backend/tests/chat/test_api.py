"""AC #3 (API), AC #4 (selected set), AC #5 (audit)."""

import uuid
from collections.abc import Collection, Iterator

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.app import create_app
from app.audit.testing import assert_no_content
from app.auth.models import Role
from app.chat import selected_set
from app.core.db import transaction
from app.spaces.access import AccessContext
from tests.auth.conftest import AuthEnv, audit_rows, make_client
from tests.chat.conftest import signed_in
from tests.spaces.conftest import make_user, make_workspace

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("spaces_db")]

CONVERSATIONS = "/api/v1/conversations"


async def private_space_of(client: httpx.AsyncClient) -> str:
    response = await client.get("/api/v1/spaces")
    return next(s["id"] for s in response.json()["items"] if s["kind"] == "private")


async def test_create_get_rename_list(auth_env: AuthEnv) -> None:
    await make_user("anika")
    async with signed_in("anika") as client:
        space_id = await private_space_of(client)
        created = await client.post(CONVERSATIONS, json={"space_id": space_id})
        assert created.status_code == 201
        body = created.json()
        assert body["space_id"] == space_id
        assert body["title"] is None
        assert body["document_ids"] == []
        conversation_id = body["id"]

        renamed = await client.patch(
            f"{CONVERSATIONS}/{conversation_id}", json={"title": "  Falcon   margins "}
        )
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "Falcon margins"
        fetched = (await client.get(f"{CONVERSATIONS}/{conversation_id}")).json()
        assert fetched["title"] == "Falcon margins"
        listed = (await client.get(CONVERSATIONS)).json()
    assert [c["title"] for c in listed["items"]] == ["Falcon margins"]
    assert listed["next_cursor"] is None
    (row,) = await audit_rows("chat.conversation_created")
    assert row.payload == {"conversation_id": conversation_id, "space_id": space_id}


async def test_list_is_newest_first_and_paginated(auth_env: AuthEnv) -> None:
    await make_user("bo")
    async with signed_in("bo") as client:
        space_id = await private_space_of(client)
        ids = [
            (await client.post(CONVERSATIONS, json={"space_id": space_id})).json()["id"]
            for _ in range(5)
        ]
        # Touching the oldest makes it the most recent.
        await client.patch(f"{CONVERSATIONS}/{ids[0]}", json={"title": "Renamed"})
        seen: list[str] = []
        cursor = None
        while True:
            params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
            page = (await client.get(CONVERSATIONS, params=params)).json()
            seen += [c["id"] for c in page["items"]]
            cursor = page["next_cursor"]
            if cursor is None:
                break
        bad = await client.get(CONVERSATIONS, params={"cursor": "not-a-cursor"})
    assert seen == [ids[0], *reversed(ids[1:])]
    assert bad.status_code == 422


async def test_other_users_conversation_is_404(auth_env: AuthEnv) -> None:
    a = await make_user("alice")
    b = await make_user("bilal")
    workspace = await make_workspace("Falcon", a, b)
    async with signed_in("alice") as client:
        conversation_id = (
            await client.post(CONVERSATIONS, json={"space_id": str(workspace)})
        ).json()["id"]
        await client.patch(f"{CONVERSATIONS}/{conversation_id}", json={"title": "Alice only"})
    async with signed_in("bilal") as client:
        responses = [
            await client.get(f"{CONVERSATIONS}/{conversation_id}"),
            await client.patch(f"{CONVERSATIONS}/{conversation_id}", json={"title": "Mine"}),
            await client.delete(f"{CONVERSATIONS}/{conversation_id}"),
            await client.put(
                f"{CONVERSATIONS}/{conversation_id}/documents", json={"document_ids": []}
            ),
        ]
        listed = await client.get(CONVERSATIONS)
    assert [r.status_code for r in responses] == [404] * 4
    assert listed.json()["items"] == []
    assert "Alice only" not in listed.text


async def test_create_needs_membership(auth_env: AuthEnv) -> None:
    a = await make_user("alice")
    await make_user("outsider")
    workspace = await make_workspace("Falcon", a)
    closed = await make_workspace("Closed", a, closed=True)
    async with signed_in("outsider") as client:
        response = await client.post(CONVERSATIONS, json={"space_id": str(workspace)})
        unknown = await client.post(CONVERSATIONS, json={"space_id": str(uuid.uuid4())})
    async with signed_in("alice") as client:
        in_closed = await client.post(CONVERSATIONS, json={"space_id": str(closed)})
    assert [response.status_code, unknown.status_code, in_closed.status_code] == [404] * 3
    assert response.json()["code"] == "space_not_found"


async def test_delete_is_permanent(auth_env: AuthEnv) -> None:
    await make_user("cleo")
    async with signed_in("cleo") as client:
        space_id = await private_space_of(client)
        conversation_id = (await client.post(CONVERSATIONS, json={"space_id": space_id})).json()[
            "id"
        ]
        assert (await client.delete(f"{CONVERSATIONS}/{conversation_id}")).status_code == 204
        assert (await client.get(f"{CONVERSATIONS}/{conversation_id}")).status_code == 404
        assert (await client.delete(f"{CONVERSATIONS}/{conversation_id}")).status_code == 404
    (row,) = await audit_rows("chat.conversation_deleted")
    assert row.payload == {"conversation_id": conversation_id, "space_id": space_id}


async def test_owner_removed_from_workspace_gets_404(auth_env: AuthEnv) -> None:
    lead = await make_user("lead")
    a = await make_user("alice")
    workspace = await make_workspace("Falcon", lead, a)
    async with signed_in("alice") as client:
        conversation_id = (
            await client.post(CONVERSATIONS, json={"space_id": str(workspace)})
        ).json()["id"]
        async with transaction(context={"app.user_id": str(lead.id)}) as tx:
            await tx.execute(
                text("DELETE FROM space_members WHERE space_id = :s AND user_id = :u"),
                {"s": workspace, "u": a.id},
            )
        response = await client.get(f"{CONVERSATIONS}/{conversation_id}")
    assert response.status_code == 404  # not 403


@pytest.mark.parametrize("role", [Role.ADMIN, Role.COMPLIANCE])
async def test_only_role_user_has_conversations(auth_env: AuthEnv, role: Role) -> None:
    await make_user("staff", role=role)
    async with signed_in("staff") as client:
        assert (await client.get(CONVERSATIONS)).status_code == 403


async def test_requires_a_session(auth_env: AuthEnv) -> None:
    async with make_client() as client:
        assert (await client.get(CONVERSATIONS)).status_code == 401


@pytest.mark.parametrize("title", ["", "   ", "x" * 201])
async def test_invalid_titles(auth_env: AuthEnv, title: str) -> None:
    await make_user("dee")
    async with signed_in("dee") as client:
        space_id = await private_space_of(client)
        conversation_id = (await client.post(CONVERSATIONS, json={"space_id": space_id})).json()[
            "id"
        ]
        response = await client.patch(f"{CONVERSATIONS}/{conversation_id}", json={"title": title})
    assert response.status_code == 422


# --- AC #4: selected set ------------------------------------------------------------------


class FakeResolver:
    """Documents in ``known`` are usable in ``space``; everything else isn't."""

    def __init__(self, space: uuid.UUID, known: set[uuid.UUID]) -> None:
        self.space, self.known = space, known

    async def unusable(
        self,
        session: AsyncSession,
        ctx: AccessContext,
        space_id: uuid.UUID,
        document_ids: Collection[uuid.UUID],
    ) -> set[uuid.UUID]:
        if space_id != self.space:
            return set(document_ids)
        return set(document_ids) - self.known


@pytest.fixture
def no_resolver() -> Iterator[None]:
    previous = selected_set.current_resolver()
    selected_set.unregister_resolver()
    yield
    if previous is not None:
        selected_set.register_resolver(previous)


@pytest.mark.usefixtures("no_resolver")
async def test_without_a_resolver_only_the_empty_set_is_accepted(auth_env: AuthEnv) -> None:
    await make_user("eli")
    async with signed_in("eli") as client:
        space_id = await private_space_of(client)
        conversation_id = (await client.post(CONVERSATIONS, json={"space_id": space_id})).json()[
            "id"
        ]
        url = f"{CONVERSATIONS}/{conversation_id}/documents"
        empty = await client.put(url, json={"document_ids": []})
        non_empty = await client.put(url, json={"document_ids": [str(uuid.uuid4())]})
    assert (empty.status_code, empty.json()) == (200, {"document_ids": []})
    assert (non_empty.status_code, non_empty.json()["code"]) == (422, "document_not_found")


@pytest.mark.usefixtures("no_resolver")
async def test_with_a_resolver_the_set_is_replaced_and_checked(auth_env: AuthEnv) -> None:
    await make_user("fay")
    good, other = uuid.uuid4(), uuid.uuid4()
    async with signed_in("fay") as client:
        space_id = await private_space_of(client)
        selected_set.register_resolver(FakeResolver(uuid.UUID(space_id), {good, other}))
        conversation_id = (await client.post(CONVERSATIONS, json={"space_id": space_id})).json()[
            "id"
        ]
        url = f"{CONVERSATIONS}/{conversation_id}/documents"
        first = await client.put(url, json={"document_ids": [str(good), str(other), str(good)]})
        assert first.json() == {"document_ids": [str(good), str(other)]}
        second = await client.put(url, json={"document_ids": [str(other)]})
        assert second.json() == {"document_ids": [str(other)]}
        assert (await client.get(f"{CONVERSATIONS}/{conversation_id}")).json()["document_ids"] == [
            str(other)
        ]
        wrong = await client.put(url, json={"document_ids": [str(uuid.uuid4())]})
        assert (wrong.status_code, wrong.json()["code"]) == (422, "document_not_found")
        # A failed update leaves the set as it was.
        assert (await client.get(f"{CONVERSATIONS}/{conversation_id}")).json()["document_ids"] == [
            str(other)
        ]


@pytest.mark.usefixtures("no_resolver")
async def test_wrong_space_document_is_rejected(auth_env: AuthEnv) -> None:
    a = await make_user("gus")
    workspace = await make_workspace("Falcon", a)
    doc = uuid.uuid4()
    selected_set.register_resolver(FakeResolver(workspace, {doc}))
    async with signed_in("gus") as client:
        space_id = await private_space_of(client)
        conversation_id = (await client.post(CONVERSATIONS, json={"space_id": space_id})).json()[
            "id"
        ]
        response = await client.put(
            f"{CONVERSATIONS}/{conversation_id}/documents", json={"document_ids": [str(doc)]}
        )
    assert response.status_code == 422


async def test_audit_rows_are_metadata_only(auth_env: AuthEnv) -> None:
    await make_user("hal")
    async with signed_in("hal") as client:
        space_id = await private_space_of(client)
        conversation_id = (await client.post(CONVERSATIONS, json={"space_id": space_id})).json()[
            "id"
        ]
        await client.patch(f"{CONVERSATIONS}/{conversation_id}", json={"title": "Planted 5521"})
        await client.delete(f"{CONVERSATIONS}/{conversation_id}")
    for row in await audit_rows():
        assert_no_content(row.payload)
        assert "Planted" not in str(row.payload)


def test_paths_in_openapi() -> None:
    paths = create_app(configure_logs=False, readiness_checks=False).openapi()["paths"]
    assert {
        "/api/v1/conversations",
        "/api/v1/conversations/{conversation_id}",
        "/api/v1/conversations/{conversation_id}/documents",
    } <= set(paths)
