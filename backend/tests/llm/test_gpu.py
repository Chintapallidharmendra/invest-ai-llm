"""Optional end-to-end check against the real vLLM servers (UAT VM; Story 1.4).

    export APP_TEST_VLLM_BASE_URLS='{"chat": "http://vllm-chat:8000/v1",
                                     "guard": "http://vllm-guard:8000/v1"}'
    uv run pytest -m gpu tests/llm/test_gpu.py

Run it from a container on the ``inference`` network. Skipped unless the variable is set.
"""

import json
import os

import pytest

from app.llm import Gateway, RequestPriority, TextDelta
from app.llm.settings import LLMSettings
from tests.conftest import LLM_SALT_SECRET
from tests.llm.conftest import USER_A

pytestmark = pytest.mark.gpu


@pytest.fixture
def real_settings(monkeypatch: pytest.MonkeyPatch) -> LLMSettings:
    urls = os.environ.get("APP_TEST_VLLM_BASE_URLS")
    if not urls:
        pytest.skip("APP_TEST_VLLM_BASE_URLS not set (real vLLM only on the UAT VM)")
    monkeypatch.setenv("APP_LLM_BASE_URLS", json.dumps(json.loads(urls)))
    monkeypatch.setenv("APP_LLM_CACHE_SALT_SECRET", LLM_SALT_SECRET)
    return LLMSettings()


async def test_real_vllm_round_trip(real_settings: LLMSettings) -> None:
    async with Gateway(real_settings) as gw:
        assert await gw.health("chat")
        assert await gw.health("guard")
        result = await gw.chat_completion(
            [{"role": "user", "content": "Reply with the single word: ready"}],
            salt_subject=USER_A,
            priority=RequestPriority.CHAT,
            max_tokens=16,
        )
        assert result.content
        assert "<think>" not in result.content
        streamed = ""
        async for event in gw.stream_chat(
            [{"role": "user", "content": "Count from 1 to 3."}],
            salt_subject=USER_A,
            priority=RequestPriority.BACKGROUND,
            max_tokens=32,
        ):
            if isinstance(event, TextDelta):
                streamed += event.text
        assert streamed
        verdict = await gw.guard_classify(
            "Summarise this synthetic term sheet.",
            salt_subject=USER_A,
            priority=RequestPriority.CHAT,
        )
        assert verdict.is_safe
