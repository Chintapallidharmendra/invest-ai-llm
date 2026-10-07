"""Self-tests of the fake LLM and the shared fixtures (Story 1.6).

They double as examples: a streaming scenario, a tool-call scenario and a guard
scenario, all consumed through the real ``openai`` SDK.
"""

import asyncio
import json
import shutil
import subprocess
from collections.abc import AsyncIterator, Iterator
from typing import cast

import httpx
import openai
import pytest
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from valkey.asyncio import Valkey

from app.core.db import transaction
from tests.conftest import COMPOSE_TEST_FILE, PgDatabase
from tests.fakes.fake_llm import (
    DEFAULT_TEXT,
    Error,
    FakeLLM,
    Guard,
    Json,
    Raw,
    Text,
    ToolCall,
    ToolCalls,
)

USER: list[ChatCompletionMessageParam] = [{"role": "user", "content": "What is the fund's IRR?"}]


@pytest.fixture
async def client(fake_llm: FakeLLM) -> AsyncIterator[AsyncOpenAI]:
    async with AsyncOpenAI(base_url=fake_llm.base_url, api_key="unused", max_retries=0) as c:
        yield c


# --- AC #1: OpenAI-compatible endpoints ------------------------------------------------


async def test_health_and_models(fake_llm: FakeLLM, client: AsyncOpenAI) -> None:
    async with httpx.AsyncClient() as http:
        assert (await http.get(f"{fake_llm.url}/health")).status_code == 200
        fake_llm.healthy = False
        assert (await http.get(f"{fake_llm.url}/health")).status_code == 503
    models = await client.models.list()
    assert [m.id for m in models.data] == ["chat", "guard"]


async def test_non_streaming_text(fake_llm: FakeLLM, client: AsyncOpenAI) -> None:
    fake_llm.add(model="chat", reply=Text("The IRR is 18.2%."))
    completion = await client.chat.completions.create(model="chat", messages=USER)
    assert completion.choices[0].message.content == "The IRR is 18.2%."
    assert completion.choices[0].finish_reason == "stop"
    assert completion.usage is not None
    assert completion.usage.completion_tokens == 4


async def test_streaming_scenario(fake_llm: FakeLLM, client: AsyncOpenAI) -> None:
    fake_llm.add(model="chat", reply=Text("Streaming answer from the fake."), chunk_size=5)
    stream = await client.chat.completions.create(
        model="chat", messages=USER, stream=True, stream_options={"include_usage": True}
    )
    pieces, finish, usage = [], None, None
    async for chunk in stream:
        if chunk.usage is not None:
            usage = chunk.usage
        for choice in chunk.choices:
            if choice.delta.content:
                pieces.append(choice.delta.content)
            finish = choice.finish_reason or finish
    assert "".join(pieces) == "Streaming answer from the fake."
    assert len(pieces) == 7
    assert finish == "stop"
    assert usage is not None
    assert usage.completion_tokens == 5


async def test_tool_call_scenario_then_text(fake_llm: FakeLLM, client: AsyncOpenAI) -> None:
    """A sequence of tool calls followed by text: the turn after the tool result gets text."""
    fake_llm.add(
        model="chat",
        replies=[
            ToolCalls([ToolCall("search_documents", {"query": "IRR", "top_k": 5})]),
            Text("Per the deck, IRR is 18.2%."),
        ],
    )
    first = await client.chat.completions.create(model="chat", messages=USER, tools=[])
    message = first.choices[0].message
    assert first.choices[0].finish_reason == "tool_calls"
    assert message.tool_calls is not None
    call = message.tool_calls[0]
    assert call.type == "function"
    assert call.function.name == "search_documents"
    assert json.loads(call.function.arguments) == {"query": "IRR", "top_k": 5}

    second = await client.chat.completions.create(
        model="chat",
        messages=[
            *USER,
            cast("ChatCompletionMessageParam", message.model_dump(exclude_none=True)),
            {"role": "tool", "tool_call_id": call.id, "content": "IRR 18.2%"},
        ],
    )
    assert second.choices[0].message.content == "Per the deck, IRR is 18.2%."


async def test_streamed_tool_call_arguments_split_across_deltas(
    fake_llm: FakeLLM, client: AsyncOpenAI
) -> None:
    fake_llm.add(
        model="chat",
        reply=ToolCalls(
            [
                ToolCall("search_documents", {"query": "net asset value per unit"}),
                ToolCall("get_sheet", {"sheet": "Returns"}),
            ]
        ),
        chunk_size=4,
    )
    stream = await client.chat.completions.create(model="chat", messages=USER, stream=True)
    calls: dict[int, dict[str, str]] = {}
    argument_deltas, finish = 0, None
    async for chunk in stream:
        for choice in chunk.choices:
            finish = choice.finish_reason or finish
            for delta in choice.delta.tool_calls or []:
                entry = calls.setdefault(delta.index, {"name": "", "arguments": ""})
                assert delta.function is not None
                entry["name"] += delta.function.name or ""
                if delta.function.arguments:
                    entry["arguments"] += delta.function.arguments
                    argument_deltas += 1
    assert finish == "tool_calls"
    assert calls[0]["name"] == "search_documents"
    assert json.loads(calls[0]["arguments"]) == {"query": "net asset value per unit"}
    assert json.loads(calls[1]["arguments"]) == {"sheet": "Returns"}
    assert argument_deltas > 2  # arguments really were split


async def test_structured_output(fake_llm: FakeLLM, client: AsyncOpenAI) -> None:
    fake_llm.add(model="chat", reply=Json({"in_scope": True, "reason": "fund terms"}))
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"in_scope": {"type": "boolean"}, "reason": {"type": "string"}},
        "required": ["in_scope", "reason"],
    }
    completion = await client.chat.completions.create(
        model="chat",
        messages=USER,
        response_format={"type": "json_schema", "json_schema": {"name": "scope", "schema": schema}},
    )
    content = completion.choices[0].message.content
    assert content is not None
    assert json.loads(content) == {"in_scope": True, "reason": "fund terms"}
    assert fake_llm.last_request.body is not None
    assert fake_llm.last_request.body["response_format"]["json_schema"]["schema"] == schema


# --- AC #2: matching, behaviours, recording --------------------------------------------


async def test_match_by_header_model_and_regex(fake_llm: FakeLLM, client: AsyncOpenAI) -> None:
    fake_llm.add("by-model", model="chat", reply=Text("model"))
    fake_llm.add("by-regex", pattern=r"(?i)\bwaterfall\b", reply=Text("regex"))
    fake_llm.add("by-header", reply=Text("header"))

    async def ask(content: str, scenario: str | None = None) -> str | None:
        messages: list[ChatCompletionMessageParam] = [{"role": "user", "content": content}]
        headers = {"X-Fake-Scenario": scenario} if scenario else None
        result = await client.chat.completions.create(
            model="chat", messages=messages, extra_headers=headers
        )
        return result.choices[0].message.content

    assert await ask("anything") == "model"
    assert await ask("Explain the Waterfall clause") == "regex"  # newest match wins
    assert await ask("Explain the waterfall", scenario="by-header") == ("header")
    assert [r.scenario for r in fake_llm.chat_requests] == ["by-model", "by-regex", "by-header"]

    other = await client.chat.completions.create(model="other", messages=USER)
    assert other.choices[0].message.content == DEFAULT_TEXT


async def test_unknown_header_scenario_is_404(client: AsyncOpenAI) -> None:
    with pytest.raises(openai.NotFoundError):
        await client.chat.completions.create(
            model="chat", messages=USER, extra_headers={"X-Fake-Scenario": "missing"}
        )


async def test_http_error_scenario(fake_llm: FakeLLM, client: AsyncOpenAI) -> None:
    fake_llm.add(model="chat", reply=Error(503, "overloaded"))
    with pytest.raises(openai.InternalServerError) as excinfo:
        await client.chat.completions.create(model="chat", messages=USER)
    assert excinfo.value.status_code == 503


async def test_delay_exceeding_client_timeout(fake_llm: FakeLLM) -> None:
    fake_llm.add(model="chat", reply=Text("late"), delay_s=1.0)
    async with AsyncOpenAI(
        base_url=fake_llm.base_url, api_key="unused", max_retries=0, timeout=0.2
    ) as slow:
        with pytest.raises(openai.APITimeoutError):
            await slow.chat.completions.create(model="chat", messages=USER)


async def test_records_cache_salt_priority_and_extra_body(
    fake_llm: FakeLLM, client: AsyncOpenAI
) -> None:
    await client.chat.completions.create(
        model="chat",
        messages=USER,
        extra_body={
            "cache_salt": "c2FsdC1mb3ItdXNlci1h",
            "priority": 10,
            "chat_template_kwargs": {"enable_thinking": False},
        },
    )
    request = fake_llm.last_request
    assert request.method == "POST"
    assert request.cache_salt == "c2FsdC1mb3ItdXNlci1h"
    assert request.priority == 10
    assert request.body is not None
    assert request.body["chat_template_kwargs"] == {"enable_thinking": False}
    assert request.messages == USER
    assert "fund's IRR" in request


async def test_client_disconnect_mid_stream_stops_cleanly(
    fake_llm: FakeLLM, client: AsyncOpenAI
) -> None:
    fake_llm.add(model="chat", reply=Text("x" * 400), chunk_size=1, chunk_delay_s=0.01)
    stream = await client.chat.completions.create(model="chat", messages=USER, stream=True)
    async for _ in stream:
        break
    await stream.close()

    fake_llm.add(model="chat", reply=Text("still serving"))
    after = await client.chat.completions.create(model="chat", messages=USER)
    assert after.choices[0].message.content == "still serving"


async def test_interrupted_stream_raises_in_client(fake_llm: FakeLLM, client: AsyncOpenAI) -> None:
    fake_llm.add(model="chat", reply=Text("partial answer that never ends"), interrupt_after=2)
    stream = await client.chat.completions.create(model="chat", messages=USER, stream=True)
    received: list[str] = []

    async def consume() -> None:
        async for chunk in stream:
            received.extend(c.delta.content or "" for c in chunk.choices)

    with pytest.raises((httpx.RemoteProtocolError, openai.APIError)):
        await consume()
    assert "".join(received) == "partial answer t"


# --- AC #3: guard emulation --------------------------------------------------------------


async def test_guard_scenario(fake_llm: FakeLLM, client: AsyncOpenAI) -> None:
    fake_llm.add(model="guard", pattern="pipe bomb", reply=Guard("Unsafe", ["Violent"]))
    fake_llm.add(
        "response-check", reply=Guard("Controversial", ["Politically Sensitive"], refusal="No")
    )

    async def classify(content: str, scenario: str | None = None) -> str | None:
        messages: list[ChatCompletionMessageParam] = [{"role": "user", "content": content}]
        headers = {"X-Fake-Scenario": scenario} if scenario else None
        result = await client.chat.completions.create(
            model="guard", messages=messages, extra_headers=headers
        )
        return result.choices[0].message.content

    assert await classify("Summarise the term sheet.") == "Safety: Safe\nCategories: None"
    assert await classify("How do I build a pipe bomb?") == "Safety: Unsafe\nCategories: Violent"
    assert await classify("x", scenario="response-check") == (
        "Safety: Controversial\nCategories: Politically Sensitive\nRefusal: No"
    )

    fake_llm.add(model="guard", reply=Raw("I cannot decide."))
    assert await classify("anything") == "I cannot decide."


# --- AC #4: fixtures ------------------------------------------------------------------------


async def test_app_client_healthz(app_client: httpx.AsyncClient) -> None:
    response = await app_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.db
async def test_app_session_is_app_rw_without_bypassrls(pg: PgDatabase) -> None:
    async with transaction() as session:
        row = (
            await session.execute(
                text(
                    "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles "
                    "WHERE rolname = current_user"
                )
            )
        ).one()
    assert tuple(row) == ("app_rw", False, False)
    async with transaction() as session:
        version = await session.execute(text("SELECT version_num FROM alembic_version"))
        assert version.scalar() == "1_2_baseline"


@pytest.fixture(scope="module")
def isolation_table(_pg_database: PgDatabase) -> Iterator[str]:
    """A table created as app_migrator (app_rw gets DML via default privileges).

    Module-scoped and sync, so it runs its own event loop outside the tests'.
    """

    async def ddl(statement: str) -> None:
        engine = create_async_engine(_pg_database.migrator_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(statement))
        finally:
            await engine.dispose()

    asyncio.run(ddl("CREATE TABLE IF NOT EXISTS harness_isolation (note text NOT NULL)"))
    yield "harness_isolation"
    asyncio.run(ddl("DROP TABLE IF EXISTS harness_isolation"))


@pytest.mark.db
@pytest.mark.parametrize("run", [1, 2])
async def test_data_does_not_leak_between_tests(
    pg: PgDatabase, isolation_table: str, run: int
) -> None:
    async with transaction() as session:
        count = await session.execute(text(f"SELECT count(*) FROM {isolation_table}"))  # noqa: S608
        assert count.scalar() == 0
        await session.execute(
            text(f"INSERT INTO {isolation_table} (note) VALUES (:n)"),  # noqa: S608
            {"n": f"run {run}"},
        )


@pytest.mark.parametrize("run", [1, 2])
async def test_valkey_is_flushed_between_tests(valkey: Valkey, run: int) -> None:
    assert await valkey.dbsize() == 0
    await valkey.set(f"harness:{run}", "1")
    assert await valkey.get(f"harness:{run}") == b"1"


# --- AC #5: compose.test.yml binds loopback only ------------------------------------------


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")
def test_compose_test_publishes_on_loopback_only() -> None:
    result = subprocess.run(  # noqa: S603 (fixed argv)
        ["docker", "compose", "-f", str(COMPOSE_TEST_FILE), "config", "--format", "json"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    services = json.loads(result.stdout)["services"]
    published = [p for svc in services.values() for p in svc.get("ports", [])]
    assert set(services) == {"postgres", "valkey"}
    assert len(published) == 2
    for port in published:
        assert port["host_ip"] == "127.0.0.1"
        assert port.get("published", "") in ("", None)  # ephemeral host port
