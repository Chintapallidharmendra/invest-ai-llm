"""Spans and logs are content-free (Story 1.5, AC #6; ADR-033)."""

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel

from app.llm import Gateway, LLMTimeout, LLMUnavailable, RequestPriority, TextDelta
from app.llm.settings import LLMSettings
from tests.core.conftest import LogCapture
from tests.fakes.fake_llm import Error, FakeLLM, Guard, Json, Text
from tests.llm.conftest import CANARY, USER_A, llm_spans, profiles_with, span_attributes

PROMPT = [{"role": "user", "content": f"Summarise {CANARY} for the committee."}]
ALLOWED_SPAN_KEYS = {
    "llm_model",
    "llm_served_model",
    "llm_priority",
    "llm_outcome",
    "latency_ms",
    "prompt_token_count",
    "completion_token_count",
    "error_code",
}


class Answer(BaseModel):
    text: str


async def test_spans_and_logs_have_no_content(
    fake_llm: FakeLLM, gateway: Gateway, spans: InMemorySpanExporter, logs: LogCapture
) -> None:
    fake_llm.add(model="chat", pattern="structured", reply=Json({"text": f"echo {CANARY}"}))
    fake_llm.add(model="chat", pattern="committee", reply=Text(f"Reply mentioning {CANARY}."))
    fake_llm.add(model="guard", reply=Guard("Unsafe", [CANARY]))
    structured_prompt = [{"role": "user", "content": f"structured {CANARY}"}]

    result = await gateway.chat_completion(
        PROMPT, salt_subject=USER_A, priority=RequestPriority.CHAT
    )
    assert result.content is not None
    assert CANARY in result.content  # the canary really travelled through the gateway
    async for event in gateway.stream_chat(
        PROMPT, salt_subject=USER_A, priority=RequestPriority.CHAT
    ):
        assert isinstance(event, TextDelta) or event.finish_reason == "stop"
    await gateway.structured(
        structured_prompt, Answer, salt_subject=USER_A, priority=RequestPriority.BACKGROUND
    )
    await gateway.guard_classify(CANARY, salt_subject=USER_A, priority=RequestPriority.CHAT)

    finished = llm_spans(spans)
    assert [s.name for s in finished] == [
        "llm.chat_completion",
        "llm.stream_chat",
        "llm.structured",
        "llm.guard_classify",
    ]
    for attributes in span_attributes(spans):
        assert set(attributes) <= ALLOWED_SPAN_KEYS
        assert attributes["llm_outcome"] == "ok"
        assert isinstance(attributes["latency_ms"], int)
        assert attributes["completion_token_count"] > 0
        assert CANARY not in repr(attributes)
    first = span_attributes(spans)[0]
    assert (first["llm_model"], first["llm_served_model"], first["llm_priority"]) == (
        "chat",
        "chat",
        0,
    )
    assert span_attributes(spans)[2]["llm_priority"] == 10
    for span in finished:
        assert not span.events  # no exception events or content events

    events = [e["event"] for e in logs.events if e["event"].startswith("llm.")]
    assert events == [
        "llm.chat_completion",
        "llm.stream_chat",
        "llm.structured",
        "llm.guard_classify",
    ]
    assert CANARY not in logs.text


async def test_failures_are_content_free(
    fake_llm: FakeLLM,
    gateway: Gateway,
    llm_settings: LLMSettings,
    spans: InMemorySpanExporter,
    logs: LogCapture,
) -> None:
    fake_llm.add(model="chat", reply=Error(503, f"server echoed {CANARY}"))
    with pytest.raises(LLMUnavailable):
        await gateway.chat_completion(PROMPT, salt_subject=USER_A, priority=RequestPriority.CHAT)

    fake_llm.add(model="guard", reply=Guard(), delay_s=0.5)
    profiles = profiles_with(llm_settings, "guard", timeouts={"total_s": 0.1})
    async with Gateway(llm_settings, profiles) as gw:
        with pytest.raises(LLMTimeout):
            await gw.guard_classify(CANARY, salt_subject=USER_A, priority=RequestPriority.CHAT)

    unavailable, timeout = span_attributes(spans)
    assert unavailable["llm_outcome"] == unavailable["error_code"] == "llm_unavailable"
    assert timeout["llm_outcome"] == timeout["error_code"] == "llm_timeout"
    for attributes in (unavailable, timeout):
        assert set(attributes) <= ALLOWED_SPAN_KEYS
    for span in llm_spans(spans):
        assert span.status.status_code.name == "ERROR"
        assert not span.events
        assert CANARY not in (span.status.description or "")
    llm_events = [e for e in logs.events if e["event"].startswith("llm.")]
    assert [e.get("error_code") for e in llm_events] == [
        "llm_unavailable",
        "llm_timeout",
    ]
    assert CANARY not in logs.text
