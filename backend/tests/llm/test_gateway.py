"""Gateway behaviour against the fake LLM (Story 1.5, AC #1-#5)."""

import asyncio
import socket
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.core import readiness
from app.llm import (
    Gateway,
    GuardVerdict,
    LLMConfigError,
    LLMRequestError,
    LLMSchemaError,
    LLMStreamInterrupted,
    LLMTimeout,
    LLMUnavailable,
    MissingCacheSalt,
    RequestPriority,
    StreamEnd,
    StreamEvent,
    TextDelta,
)
from app.llm import readiness as llm_readiness
from app.llm.settings import DEFAULT_PROFILES_PATH, LLMSettings
from tests.fakes.fake_llm import Error, FakeLLM, Guard, Json, Raw, Text, ToolCall, ToolCalls
from tests.llm.conftest import USER_A, USER_B, expected_salt, profiles_with

CHAT = RequestPriority.CHAT
BACKGROUND = RequestPriority.BACKGROUND
ASK = [{"role": "user", "content": "What is the hurdle rate in the LPA?"}]
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": "Search the space's documents.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    }
]


class Scope(BaseModel):
    in_scope: bool
    reason: str


async def collect(events: AsyncIterator[StreamEvent]) -> tuple[str, StreamEnd]:
    text, end = "", None
    async for event in events:
        if isinstance(event, TextDelta):
            text += event.text
        else:
            end = event
    assert end is not None
    return text, end


# --- AC #1: the four operations ----------------------------------------------------


async def test_chat_completion(fake_llm: FakeLLM, gateway: Gateway) -> None:
    fake_llm.add(model="chat", reply=Text("The hurdle rate is 8%."))
    result = await gateway.chat_completion(ASK, salt_subject=USER_A, priority=CHAT)
    assert result.content == "The hurdle rate is 8%."
    assert result.finish_reason == "stop"
    assert result.tool_calls == []
    assert result.usage.completion_tokens == 5
    request = fake_llm.last_request
    assert request.model == "chat"
    assert request.messages == ASK
    assert request.body is not None
    assert request.body["max_tokens"] == 1024


async def test_chat_completion_tool_calls(fake_llm: FakeLLM, gateway: Gateway) -> None:
    fake_llm.add(model="chat", reply=ToolCalls([ToolCall("search_documents", {"query": "hurdle"})]))
    result = await gateway.chat_completion(
        ASK, salt_subject=USER_A, priority=CHAT, tools=TOOLS, tool_choice="auto"
    )
    assert result.finish_reason == "tool_calls"
    assert [(c.name, c.arguments) for c in result.tool_calls] == [
        ("search_documents", '{"query": "hurdle"}')
    ]
    body = fake_llm.last_request.body
    assert body is not None
    assert body["tools"] == TOOLS
    assert body["tool_choice"] == "auto"


async def test_stream_chat(fake_llm: FakeLLM, gateway: Gateway) -> None:
    fake_llm.add(model="chat", reply=Text("Eight percent, compounded annually."), chunk_size=6)
    text, end = await collect(gateway.stream_chat(ASK, salt_subject=USER_A, priority=CHAT))
    assert text == "Eight percent, compounded annually."
    assert end.finish_reason == "stop"
    assert end.usage.completion_tokens == 4
    body = fake_llm.last_request.body
    assert body is not None
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}


async def test_stream_chat_tool_calls(fake_llm: FakeLLM, gateway: Gateway) -> None:
    fake_llm.add(
        model="chat",
        reply=ToolCalls([ToolCall("search_documents", {"query": "hurdle rate"}, id="call_1")]),
        chunk_size=3,
    )
    text, end = await collect(
        gateway.stream_chat(ASK, salt_subject=USER_A, priority=CHAT, tools=TOOLS)
    )
    assert text == ""
    assert end.finish_reason == "tool_calls"
    assert [(c.id, c.name, c.arguments) for c in end.tool_calls] == [
        ("call_1", "search_documents", '{"query": "hurdle rate"}')
    ]


async def test_structured(fake_llm: FakeLLM, gateway: Gateway) -> None:
    fake_llm.add(model="chat", reply=Json({"in_scope": True, "reason": "fund terms"}))
    result = await gateway.structured(ASK, Scope, salt_subject=USER_A, priority=CHAT)
    assert result == Scope(in_scope=True, reason="fund terms")
    body = fake_llm.last_request.body
    assert body is not None
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "Scope", "schema": Scope.model_json_schema(), "strict": True},
    }


@pytest.mark.parametrize(
    "reply",
    [Json({"in_scope": "maybe"}), Raw('{"in_scope": true, "reason": '), Raw("")],
    ids=["schema-mismatch", "truncated-json", "empty"],
)
async def test_structured_invalid_output(
    fake_llm: FakeLLM, gateway: Gateway, reply: Json | Raw
) -> None:
    fake_llm.add(model="chat", reply=reply)
    with pytest.raises(LLMSchemaError) as excinfo:
        await gateway.structured(ASK, Scope, salt_subject=USER_A, priority=CHAT)
    assert excinfo.value.__cause__ is None  # the validation error (quoting output) is dropped
    assert len(fake_llm.chat_requests) == 1  # never retried on content


@pytest.mark.parametrize(
    ("reply", "verdict"),
    [
        (Guard(), GuardVerdict("safe")),
        (Guard("Unsafe", ["Violent"]), GuardVerdict("unsafe", frozenset({"Violent"}))),
        (
            Guard("Controversial", ["Politically Sensitive Topics"]),
            GuardVerdict("controversial", frozenset({"Politically Sensitive Topics"})),
        ),
        (Raw("no idea"), GuardVerdict("controversial", frozenset({"parse_error"}))),
    ],
    ids=["safe", "unsafe", "controversial", "garbage"],
)
async def test_guard_classify_prompt(
    fake_llm: FakeLLM, gateway: Gateway, reply: Guard | Raw, verdict: GuardVerdict
) -> None:
    fake_llm.add(model="guard", reply=reply)
    result = await gateway.guard_classify(
        "Is this OK?", "prompt", salt_subject=USER_A, priority=CHAT
    )
    assert result == verdict
    request = fake_llm.last_request
    assert request.model == "guard"
    assert request.messages == [{"role": "user", "content": "Is this OK?"}]
    assert request.body is not None
    assert request.body["max_tokens"] == 32
    assert request.body["temperature"] == 0
    assert "chat_template_kwargs" not in request.body


async def test_guard_classify_response(fake_llm: FakeLLM, gateway: Gateway) -> None:
    fake_llm.add(model="guard", reply=Guard("Safe", refusal="Yes"))
    result = await gateway.guard_classify(
        "I can't help with that.", "response", salt_subject=USER_A, priority=CHAT, prompt="Q?"
    )
    assert result == GuardVerdict("safe", frozenset(), True)
    assert fake_llm.last_request.messages == [
        {"role": "user", "content": "Q?"},
        {"role": "assistant", "content": "I can't help with that."},
    ]


# --- AC #2: cache salt ----------------------------------------------------------------


async def test_cache_salt_per_user_on_every_call(fake_llm: FakeLLM, gateway: Gateway) -> None:
    fake_llm.add(model="chat", pattern="structured", reply=Json({"in_scope": True, "reason": "x"}))
    await gateway.chat_completion(ASK, salt_subject=USER_A, priority=CHAT)
    await collect(gateway.stream_chat(ASK, salt_subject=USER_A, priority=CHAT))
    await gateway.structured(
        [{"role": "user", "content": "structured"}], Scope, salt_subject=USER_A, priority=CHAT
    )
    await gateway.guard_classify("x", salt_subject=USER_A, priority=CHAT)
    await gateway.chat_completion(ASK, salt_subject=USER_B, priority=CHAT)

    salts = [r.cache_salt for r in fake_llm.chat_requests]
    assert salts[:4] == [expected_salt(USER_A)] * 4
    assert salts[4] == expected_salt(USER_B)
    assert salts[4] != salts[0]


@pytest.mark.parametrize("subject", [None, ""])
async def test_missing_salt_raises_before_sending(
    fake_llm: FakeLLM, gateway: Gateway, subject: str | None
) -> None:
    with pytest.raises(MissingCacheSalt):
        await gateway.chat_completion(ASK, salt_subject=subject, priority=CHAT)
    with pytest.raises(MissingCacheSalt):
        gateway.stream_chat(ASK, salt_subject=subject, priority=CHAT)
    with pytest.raises(MissingCacheSalt):
        await gateway.structured(ASK, Scope, salt_subject=subject, priority=CHAT)
    with pytest.raises(MissingCacheSalt):
        await gateway.guard_classify("x", salt_subject=subject, priority=CHAT)
    with pytest.raises(MissingCacheSalt):
        await gateway.chat_completion(ASK, priority=CHAT)  # omitted entirely
    assert fake_llm.chat_requests == []


async def test_health_needs_no_salt(fake_llm: FakeLLM, gateway: Gateway) -> None:
    assert await gateway.health("chat")
    assert await gateway.health("guard")
    assert [r.path for r in fake_llm.requests] == ["/health", "/health"]


async def test_refuses_to_start_without_salt_support(
    fake_llm: FakeLLM, llm_settings: LLMSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles = profiles_with(llm_settings, "chat", supports_cache_salt=False)
    with pytest.raises(LLMConfigError, match="APP_LLM_PREFIX_CACHING_DISABLED"):
        Gateway(llm_settings, profiles)

    monkeypatch.setenv("APP_LLM_PREFIX_CACHING_DISABLED", "true")
    async with Gateway(LLMSettings(), profiles) as gw:
        await gw.chat_completion(ASK, salt_subject=USER_A, priority=CHAT)
        await gw.guard_classify("x", salt_subject=USER_A, priority=CHAT)
        with pytest.raises(MissingCacheSalt):  # the subject is still required
            await gw.chat_completion(ASK, priority=CHAT)
    chat, guard = fake_llm.chat_requests
    assert chat.cache_salt is None  # not sent to a server that cannot honour it
    assert guard.cache_salt == expected_salt(USER_A)


# --- AC #3: priority ---------------------------------------------------------------------


async def test_priority_sent_per_capability(
    fake_llm: FakeLLM, gateway: Gateway, llm_settings: LLMSettings
) -> None:
    await gateway.chat_completion(ASK, salt_subject=USER_A, priority=CHAT)
    await gateway.chat_completion(ASK, salt_subject=USER_A, priority=BACKGROUND)
    await gateway.guard_classify("x", salt_subject=USER_A, priority=BACKGROUND)
    chat, background, guard = fake_llm.chat_requests
    assert (chat.priority, background.priority) == (0, 10)
    assert guard.body is not None
    assert "priority" not in guard.body  # guard profile: supports_priority false

    async with Gateway(
        llm_settings, profiles_with(llm_settings, "chat", supports_priority=False)
    ) as gw:
        await gw.chat_completion(ASK, salt_subject=USER_A, priority=BACKGROUND)
    assert fake_llm.last_request.body is not None
    assert "priority" not in fake_llm.last_request.body


# --- AC #4: profiles ----------------------------------------------------------------------


async def test_profile_swap_changes_served_model(
    fake_llm: FakeLLM, llm_settings: LLMSettings
) -> None:
    fake_llm.add(model="qwen3-8b-fallback", reply=Text("from the fallback"))
    profiles = profiles_with(llm_settings, "chat", served_model="qwen3-8b-fallback")
    async with Gateway(llm_settings, profiles) as gw:
        result = await gw.chat_completion(ASK, salt_subject=USER_A, priority=CHAT)
    assert result.content == "from the fallback"
    assert fake_llm.last_request.model == "qwen3-8b-fallback"


async def test_profile_yaml_file_swap(
    fake_llm: FakeLLM, llm_settings: LLMSettings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "profiles.yaml"
    path.write_text(
        DEFAULT_PROFILES_PATH.read_text().replace("served_model: chat", "served_model: chat-14b")
    )
    monkeypatch.setenv("APP_LLM_PROFILES_PATH", str(path))
    async with Gateway(LLMSettings()) as gw:
        await gw.chat_completion(ASK, salt_subject=USER_A, priority=CHAT)
    assert fake_llm.last_request.model == "chat-14b"


async def test_chat_sends_thinking_off(fake_llm: FakeLLM, gateway: Gateway) -> None:
    await gateway.chat_completion(ASK, salt_subject=USER_A, priority=CHAT)
    await collect(gateway.stream_chat(ASK, salt_subject=USER_A, priority=CHAT))
    for request in fake_llm.chat_requests:
        assert request.body is not None
        assert request.body["chat_template_kwargs"] == {"enable_thinking": False}


# --- AC #5: timeouts, retries, errors, readiness -------------------------------------------


def _fast(settings: LLMSettings, **timeouts: float) -> Gateway:
    return Gateway(settings, profiles_with(settings, "chat", timeouts={"connect_s": 2, **timeouts}))


async def test_first_token_timeout(fake_llm: FakeLLM, llm_settings: LLMSettings) -> None:
    fake_llm.add(model="chat", reply=Text("slow"), delay_s=0.6)
    async with _fast(llm_settings, first_token_s=0.3, total_s=5) as gw:
        start = time.perf_counter()
        with pytest.raises(LLMTimeout):
            await collect(gw.stream_chat(ASK, salt_subject=USER_A, priority=CHAT))
        elapsed = time.perf_counter() - start
    assert 0.25 < elapsed < 0.55
    assert len(fake_llm.chat_requests) == 1  # timeouts are not retried


async def test_first_token_just_in_time(fake_llm: FakeLLM, llm_settings: LLMSettings) -> None:
    fake_llm.add(model="chat", reply=Text("on time"), delay_s=0.1)
    async with _fast(llm_settings, first_token_s=0.5, total_s=5) as gw:
        text, _ = await collect(gw.stream_chat(ASK, salt_subject=USER_A, priority=CHAT))
    assert text == "on time"


async def test_stream_total_timeout_after_first_token(
    fake_llm: FakeLLM, llm_settings: LLMSettings
) -> None:
    """The first token is fast; the 60 s analogue (here 0.5 s) still bounds the stream."""
    fake_llm.add(model="chat", reply=Text("x" * 100), chunk_size=1, chunk_delay_s=0.02)
    received: list[str] = []

    async def consume(gw: Gateway) -> None:
        async for event in gw.stream_chat(ASK, salt_subject=USER_A, priority=CHAT):
            if isinstance(event, TextDelta):
                received.append(event.text)

    async with _fast(llm_settings, first_token_s=0.3, total_s=0.5) as gw:
        start = time.perf_counter()
        with pytest.raises(LLMTimeout):
            await consume(gw)
        elapsed = time.perf_counter() - start
    assert 0.45 < elapsed < 0.8
    assert 0 < len(received) < 100


async def test_non_streaming_total_timeout(fake_llm: FakeLLM, llm_settings: LLMSettings) -> None:
    fake_llm.add(model="chat", reply=Text("late"), delay_s=0.6)
    async with _fast(llm_settings, first_token_s=0.1, total_s=0.3) as gw:
        with pytest.raises(LLMTimeout):
            await gw.chat_completion(ASK, salt_subject=USER_A, priority=CHAT)


async def test_guard_total_timeout_is_3s_in_profile(
    fake_llm: FakeLLM, llm_settings: LLMSettings
) -> None:
    fake_llm.add(model="guard", reply=Guard(), delay_s=0.5)
    profiles = profiles_with(llm_settings, "guard", timeouts={"connect_s": 2, "total_s": 0.3})
    async with Gateway(llm_settings, profiles) as gw:
        start = time.perf_counter()
        with pytest.raises(LLMTimeout):
            await gw.guard_classify("x", salt_subject=USER_A, priority=CHAT)
        assert time.perf_counter() - start < 0.5


class DroppingServer:
    """Accepts TCP connections and closes them at once (a connection error)."""

    def __init__(self) -> None:
        self.connections = 0
        self._server: asyncio.Server | None = None

    async def __aenter__(self) -> str:
        async def handle(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            self.connections += 1
            writer.close()

        self._server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}/v1"

    async def __aexit__(self, *_: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()


async def test_connection_error_retried_once(llm_settings: LLMSettings) -> None:
    server = DroppingServer()
    async with server as url:
        profiles = profiles_with(llm_settings, "chat", base_url=url)
        async with Gateway(llm_settings, profiles) as gw:
            with pytest.raises(LLMUnavailable):
                await gw.chat_completion(ASK, salt_subject=USER_A, priority=CHAT)
            assert server.connections == 2  # one try + one retry
            with pytest.raises(LLMUnavailable):
                await collect(gw.stream_chat(ASK, salt_subject=USER_A, priority=CHAT))
            assert server.connections == 4


def _closed_port_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}/v1"


async def test_connection_refused_is_unavailable(llm_settings: LLMSettings) -> None:
    profiles = profiles_with(llm_settings, "guard", base_url=_closed_port_url())
    async with Gateway(llm_settings, profiles) as gw:
        with pytest.raises(LLMUnavailable):
            await gw.guard_classify("x", salt_subject=USER_A, priority=CHAT)
        assert not await gw.health("guard")


@pytest.mark.parametrize(
    ("status", "error"),
    [(503, LLMUnavailable), (500, LLMUnavailable), (429, LLMUnavailable), (400, LLMRequestError)],
)
async def test_http_errors_typed_and_not_retried(
    fake_llm: FakeLLM, gateway: Gateway, status: int, error: type[Exception]
) -> None:
    fake_llm.add(model="chat", reply=Error(status, "echo of the prompt: CANARY-LLM-123"))
    with pytest.raises(error) as excinfo:
        await gateway.chat_completion(ASK, salt_subject=USER_A, priority=CHAT)
    assert "CANARY" not in str(excinfo.value)
    assert len(fake_llm.chat_requests) == 1


async def test_stream_interrupted_mid_way(fake_llm: FakeLLM, gateway: Gateway) -> None:
    fake_llm.add(model="chat", reply=Text("partial answer that never ends"), interrupt_after=2)
    received: list[str] = []

    async def consume() -> None:
        async for event in gateway.stream_chat(ASK, salt_subject=USER_A, priority=CHAT):
            if isinstance(event, TextDelta):
                received.append(event.text)

    with pytest.raises(LLMStreamInterrupted):
        await consume()
    assert "".join(received) == "partial answer t"  # the caller discards this
    assert len(fake_llm.chat_requests) == 1


async def test_stream_error_status_before_start(fake_llm: FakeLLM, gateway: Gateway) -> None:
    fake_llm.add(model="chat", reply=Error(503))
    with pytest.raises(LLMUnavailable) as excinfo:
        await collect(gateway.stream_chat(ASK, salt_subject=USER_A, priority=CHAT))
    assert not isinstance(excinfo.value, LLMStreamInterrupted)


async def test_consumer_stops_early(fake_llm: FakeLLM, gateway: Gateway) -> None:
    fake_llm.add(model="chat", reply=Text("x" * 200), chunk_size=1, chunk_delay_s=0.005)
    events = gateway.stream_chat(ASK, salt_subject=USER_A, priority=CHAT)
    async for _ in events:
        break
    await events.aclose()  # type: ignore[attr-defined]
    result = await gateway.chat_completion(ASK, salt_subject=USER_A, priority=CHAT)
    assert result.content


async def test_readiness_checks(fake_llm: FakeLLM, gateway: Gateway) -> None:
    llm_readiness.register_checks(lambda: gateway)
    llm_readiness.register_checks(lambda: gateway)  # idempotent
    try:
        assert await readiness.failing_checks() == []
        fake_llm.healthy = False
        assert await readiness.failing_checks() == ["llm.chat", "llm.guard"]
    finally:
        llm_readiness.unregister_checks()
    assert await readiness.failing_checks() == []


async def test_readiness_fails_when_server_down(llm_settings: LLMSettings) -> None:
    down = _closed_port_url()
    profiles = profiles_with(llm_settings, "chat", base_url=down)
    async with Gateway(llm_settings, profiles) as gw:
        llm_readiness.register_checks(lambda: gw)
        try:
            assert await readiness.failing_checks() == ["llm.chat"]
        finally:
            llm_readiness.unregister_checks()
