"""PydanticAI model factory (Story 1.5, AC #7)."""

import pytest
from pydantic_ai import Agent

from app.llm import Gateway, MissingCacheSalt, RequestPriority
from app.llm.pydantic_ai import pydantic_ai_model
from tests.fakes.fake_llm import FakeLLM, Text, ToolCall, ToolCalls
from tests.llm.conftest import CANARY, USER_A, USER_B, expected_salt


async def test_agent_run_sends_salt_priority_and_thinking_flag(
    fake_llm: FakeLLM, gateway: Gateway
) -> None:
    fake_llm.add(model="chat", reply=Text("Agent answer."))
    model, settings = pydantic_ai_model(USER_A, RequestPriority.CHAT, gateway=gateway)
    agent = Agent(model)

    result = await agent.run(f"Question about {CANARY}", model_settings=settings)

    assert result.output == "Agent answer."
    request = fake_llm.last_request
    assert request.model == "chat"
    assert request.cache_salt == expected_salt(USER_A)
    assert request.priority == 0
    assert request.body is not None
    assert request.body["chat_template_kwargs"] == {"enable_thinking": False}
    # PydanticAI sends the newer OpenAI field name; vLLM accepts both.
    assert request.body["max_completion_tokens"] == 1024


async def test_agent_tool_loop_salts_every_request(fake_llm: FakeLLM, gateway: Gateway) -> None:
    fake_llm.add(
        model="chat",
        replies=[
            ToolCalls([ToolCall("lookup_hurdle", {"fund": "Fund II"})]),
            Text("The hurdle is 8%."),
        ],
    )
    model, settings = pydantic_ai_model(USER_B, RequestPriority.BACKGROUND, gateway=gateway)
    agent = Agent(model)

    @agent.tool_plain
    def lookup_hurdle(fund: str) -> str:
        return f"{fund}: 8%"

    result = await agent.run("What is the hurdle?", model_settings=settings)

    assert result.output == "The hurdle is 8%."
    requests = fake_llm.chat_requests
    assert len(requests) == 2
    assert {r.cache_salt for r in requests} == {expected_salt(USER_B)}
    assert {r.priority for r in requests} == {10}
    tool_result = requests[1].messages[-1]
    assert tool_result["role"] == "tool"
    assert tool_result["content"] == "Fund II: 8%"


async def test_factory_uses_the_gateway_client(gateway: Gateway) -> None:
    model, _ = pydantic_ai_model(USER_A, RequestPriority.CHAT, gateway=gateway)
    assert model.client is gateway.openai_client("chat")
    assert model.model_name == "chat"


@pytest.mark.parametrize("subject", [None, ""])
def test_factory_requires_salt(gateway: Gateway, subject: str | None) -> None:
    with pytest.raises(MissingCacheSalt):
        pydantic_ai_model(subject, RequestPriority.CHAT, gateway=gateway)
