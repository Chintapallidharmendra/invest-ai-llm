"""AC #4: the probes' pass/fail decisions on fake observations and fake replies."""

import httpx
import pytest

from evals.spike import features
from evals.spike.client import Endpoint
from evals.spike.features import (
    Verdict,
    decide_cache_salt,
    decide_hermes,
    decide_priority,
    decide_thinking,
)
from tests.fakes.fake_llm import Error, FakeLLM, Text, ToolCall, ToolCalls


@pytest.mark.parametrize(
    ("same", "other", "verdict"),
    [
        (12.0, 0.0, Verdict.HONOURED),
        (12.0, 12.0, Verdict.IGNORED),
        (0.0, 0.0, Verdict.INCONCLUSIVE),  # prefix caching not working at all
        (None, None, Verdict.INCONCLUSIVE),  # metrics not exposed
    ],
)
def test_decide_cache_salt(same: float | None, other: float | None, verdict: Verdict) -> None:
    assert decide_cache_salt(same, other).verdict is verdict


def test_decide_priority() -> None:
    queued = [4.0, 4.5, 5.0, 6.0]
    assert decide_priority(None, 0.3, queued).verdict is Verdict.HONOURED
    assert decide_priority(None, 4.2, queued).verdict is Verdict.IGNORED
    assert decide_priority(400, None, []).verdict is Verdict.UNSUPPORTED
    assert decide_priority(None, None, queued).verdict is Verdict.INCONCLUSIVE
    assert decide_priority(None, 0.3, []).verdict is Verdict.INCONCLUSIVE


def test_decide_thinking() -> None:
    assert decide_thinking([{"content": "391."}, {"content": "No."}]).verdict is Verdict.PASS
    assert decide_thinking([{"content": "<think>hmm</think>391"}]).verdict is Verdict.FAIL
    leaked = {"content": "391", "reasoning_content": "17*23..."}
    assert decide_thinking([leaked]).verdict is Verdict.FAIL
    assert decide_thinking([]).verdict is Verdict.INCONCLUSIVE


def test_decide_hermes() -> None:
    good = {"tool_calls": [{"function": {"name": "list_selected_documents", "arguments": "{}"}}]}
    assert decide_hermes(good, "list_selected_documents").verdict is Verdict.PASS
    raw = {"content": '<tool_call>{"name": "list_selected_documents"}</tool_call>'}
    assert decide_hermes(raw, "list_selected_documents").verdict is Verdict.FAIL
    assert (
        decide_hermes({"content": "Two documents."}, "list_selected_documents").verdict
        is Verdict.FAIL
    )
    bad_json = {"tool_calls": [{"function": {"name": "list_selected_documents", "arguments": "{"}}]}
    assert decide_hermes(bad_json, "list_selected_documents").verdict is Verdict.FAIL
    other = {"tool_calls": [{"function": {"name": "read_pages", "arguments": "{}"}}]}
    assert decide_hermes(other, "list_selected_documents").verdict is Verdict.FAIL


async def test_probes_against_the_fake(fake_llm: FakeLLM) -> None:
    fake_llm.add(
        pattern="Which documents are selected",
        reply=ToolCalls([ToolCall("list_selected_documents", {})]),
    )
    fake_llm.add(pattern="17 times 23|EBITDA a cash", reply=Text("A short answer."))
    results = await features.run_probes(
        Endpoint(fake_llm.base_url), ["hermes", "thinking", "cache_salt"]
    )
    assert results["hermes"]["verdict"] == "pass"
    assert results["thinking"]["verdict"] == "pass"
    # The fake has no /metrics, so the cache probe can't decide.
    assert results["cache_salt"]["verdict"] == "inconclusive"
    salts = {r.cache_salt for r in fake_llm.chat_requests if r.cache_salt}
    assert len(salts) == 2


async def test_priority_probe_reports_rejection(fake_llm: FakeLLM) -> None:
    fake_llm.add(
        pattern="ping", reply=Error(400, "priority not supported", "invalid_request_error")
    )
    async with httpx.AsyncClient(timeout=30) as client:
        result = await features.probe_priority(Endpoint(fake_llm.base_url), client)
    assert result.verdict is Verdict.UNSUPPORTED


async def test_priority_probe_runs_against_the_fake(fake_llm: FakeLLM) -> None:
    # The fake doesn't schedule by priority, so the probe must not call it honoured.
    fake_llm.add(pattern="essay", reply=Text("text " * 20), delay_s=0.3)
    fake_llm.add(pattern="ready", reply=Text("yes"), delay_s=0.3)
    async with httpx.AsyncClient(timeout=30) as client:
        result = await features.probe_priority(
            Endpoint(fake_llm.base_url), client, saturate=6, max_tokens=16
        )
    assert result.verdict in {Verdict.IGNORED, Verdict.INCONCLUSIVE}
    priorities = [r.priority for r in fake_llm.chat_requests]
    assert priorities.count(10) == 6
