"""Platform feature probes (AC #4). Each probe sends a few requests and passes the
observations to a pure ``decide_*`` function, so the pass/fail logic can be tested
against fake responses.

- ``cache_salt``: vLLM's prefix-cache hit counter must rise for a repeated prompt with
  the same salt and stay flat for the same prompt under a different salt.
- ``priority``: with the server saturated by low-priority requests, a high-priority one
  must start well before the queued low-priority ones. A 400 for the field means the
  server doesn't support it.
- ``thinking``: with the profile's ``enable_thinking: false``, replies carry no
  ``<think>`` block and no ``reasoning_content``.
- ``hermes``: a request that needs a tool returns parsed ``tool_calls`` (valid JSON
  arguments), with no raw ``<tool_call>`` text in the content.
"""

import asyncio
import json
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Final

import httpx

from evals.spike.client import Endpoint, chat_body, complete, metric_sum, metrics
from evals.spike.load import BACKGROUND_PRIORITY, CHAT_PRIORITY, percentile, stream_once
from evals.spike.tool_calls.tools import TOOLS

# vLLM v1 prefix cache counters (older name as a fallback).
PREFIX_HITS: Final = ("vllm:prefix_cache_hits_total", "vllm:prefix_cache_hits")
# Long enough to fill several KV blocks (block size 16 tokens), so a hit is measurable.
SHARED_PREFIX: Final = "Background for the analyst. " + " ".join(
    f"Fact {i}: warehouse {i} in Coimbatore handles {i * 13 + 40} pallets a day."
    for i in range(120)
)


class Verdict(StrEnum):
    HONOURED = "honoured"
    IGNORED = "ignored"
    UNSUPPORTED = "unsupported"
    INCONCLUSIVE = "inconclusive"
    PASS = "pass"  # noqa: S105 (a verdict, not a password)
    FAIL = "fail"


@dataclass(frozen=True)
class ProbeResult:
    verdict: Verdict
    detail: str
    observations: Mapping[str, Any]


# --- Decisions (pure) ------------------------------------------------------------------------


def decide_cache_salt(same_salt_hits: float | None, other_salt_hits: float | None) -> ProbeResult:
    """Hit-counter deltas: repeat with the same salt, then with a different salt."""
    obs = {"same_salt_hits": same_salt_hits, "other_salt_hits": other_salt_hits}
    if same_salt_hits is None or other_salt_hits is None:
        return ProbeResult(Verdict.INCONCLUSIVE, "prefix-cache metrics not exposed", obs)
    if same_salt_hits <= 0:
        return ProbeResult(Verdict.INCONCLUSIVE, "no hits even with the same salt", obs)
    if other_salt_hits > 0:
        return ProbeResult(Verdict.IGNORED, "a different salt still hit the cache", obs)
    return ProbeResult(Verdict.HONOURED, "hits only within one salt", obs)


def decide_priority(
    rejected_status: int | None,
    high_ttft_s: float | None,
    queued_low_ttfts_s: Sequence[float],
    *,
    factor: float = 0.5,
) -> ProbeResult:
    """Honoured if the high-priority request's TTFT is under ``factor`` x the median
    TTFT of the low-priority requests queued behind the saturated batch."""
    queued_p50 = percentile(list(queued_low_ttfts_s), 50)
    obs = {
        "rejected_status": rejected_status,
        "high_ttft_s": high_ttft_s,
        "queued_low_ttft_p50_s": queued_p50,
    }
    if rejected_status is not None:
        return ProbeResult(Verdict.UNSUPPORTED, f"server answered {rejected_status}", obs)
    if high_ttft_s is None or not queued_low_ttfts_s:
        return ProbeResult(Verdict.INCONCLUSIVE, "missing timings", obs)
    if high_ttft_s < factor * queued_p50:
        return ProbeResult(Verdict.HONOURED, "high priority jumped the queue", obs)
    return ProbeResult(Verdict.IGNORED, "high priority waited like the rest", obs)


def decide_thinking(messages: Sequence[Mapping[str, Any]]) -> ProbeResult:
    leaks = [
        i
        for i, m in enumerate(messages)
        if "<think>" in (m.get("content") or "") or m.get("reasoning_content")
    ]
    obs = {"replies": len(messages), "with_thinking": leaks}
    if not messages:
        return ProbeResult(Verdict.INCONCLUSIVE, "no replies", obs)
    if leaks:
        return ProbeResult(Verdict.FAIL, "thinking output present", obs)
    return ProbeResult(Verdict.PASS, "no thinking output", obs)


def decide_hermes(message: Mapping[str, Any], expected_tool: str) -> ProbeResult:
    calls = message.get("tool_calls") or []
    content = message.get("content") or ""
    obs = {"tool_calls": len(calls), "raw_tool_call_in_content": "<tool_call>" in content}
    if "<tool_call>" in content:
        return ProbeResult(Verdict.FAIL, "tool call left unparsed in content", obs)
    if not calls:
        return ProbeResult(Verdict.FAIL, "no tool call", obs)
    function = calls[0].get("function") or {}
    try:
        arguments = json.loads(function.get("arguments") or "")
    except json.JSONDecodeError:
        return ProbeResult(Verdict.FAIL, "arguments are not JSON", obs)
    if function.get("name") != expected_tool or not isinstance(arguments, dict):
        return ProbeResult(Verdict.FAIL, f"unexpected call {function.get('name')!r}", obs)
    return ProbeResult(Verdict.PASS, "parsed tool call", obs)


# --- Probes (network) ------------------------------------------------------------------------


async def _hits(client: httpx.AsyncClient, endpoint: Endpoint) -> float | None:
    values = await metrics(client, endpoint)
    for name in PREFIX_HITS:
        if any(k == name or k.startswith(name + "{") for k in values):
            return metric_sum(values, name)
    return None


async def _prefixed(
    client: httpx.AsyncClient, endpoint: Endpoint, salt: str, question: str
) -> None:
    body = chat_body(
        endpoint,
        [{"role": "system", "content": SHARED_PREFIX}, {"role": "user", "content": question}],
        max_tokens=8,
        cache_salt=salt,
    )
    await complete(client, endpoint, body)


async def probe_cache_salt(endpoint: Endpoint, client: httpx.AsyncClient) -> ProbeResult:
    salt_a, salt_b = f"spike-{uuid.uuid4()}", f"spike-{uuid.uuid4()}"
    await _prefixed(client, endpoint, salt_a, "How many warehouses?")  # warm salt A
    before = await _hits(client, endpoint)
    await _prefixed(client, endpoint, salt_a, "Which city?")
    middle = await _hits(client, endpoint)
    await _prefixed(client, endpoint, salt_b, "Which city?")
    after = await _hits(client, endpoint)
    same = None if before is None or middle is None else middle - before
    other = None if middle is None or after is None else after - middle
    return decide_cache_salt(same, other)


async def probe_priority(
    endpoint: Endpoint, client: httpx.AsyncClient, *, saturate: int = 24, max_tokens: int = 400
) -> ProbeResult:
    """Saturate with ``saturate`` long low-priority streams (above max_num_seqs), then
    send one high-priority request."""
    probe = chat_body(endpoint, [{"role": "user", "content": "ping"}], max_tokens=1, priority=0)
    response = await client.post(f"{endpoint.base_url.rstrip('/')}/chat/completions", json=probe)
    if response.status_code == 400:  # noqa: PLR2004
        return decide_priority(400, None, [])
    lows = [
        asyncio.create_task(
            stream_once(
                client,
                endpoint,
                f"Write a long, detailed essay on logistics, part {i}.",
                max_tokens=max_tokens,
                priority=BACKGROUND_PRIORITY,
            )
        )
        for i in range(saturate)
    ]
    await asyncio.sleep(0.5)  # let the low-priority batch fill the scheduler
    high = await stream_once(
        client, endpoint, "One word: ready?", max_tokens=4, priority=CHAT_PRIORITY
    )
    low_results = await asyncio.gather(*lows)
    # The last third of the lows to start are the ones that were queued.
    started = sorted(r.ttft_s for r in low_results if r.ttft_s is not None)
    queued = started[len(started) * 2 // 3 :]
    return decide_priority(None, high.ttft_s, queued)


async def probe_thinking(endpoint: Endpoint, client: httpx.AsyncClient) -> ProbeResult:
    messages = []
    for question in ("What is 17 times 23? Explain briefly.", "Is EBITDA a cash measure?"):
        body = chat_body(endpoint, [{"role": "user", "content": question}], max_tokens=200)
        reply = await complete(client, endpoint, body)
        messages.append(reply["choices"][0]["message"])
    return decide_thinking(messages)


async def probe_hermes(endpoint: Endpoint, client: httpx.AsyncClient) -> ProbeResult:
    body = chat_body(
        endpoint,
        [
            {"role": "system", "content": "Use the tools. Never answer from memory."},
            {"role": "user", "content": "Which documents are selected?"},
        ],
        max_tokens=200,
        tools=TOOLS,
    )
    reply = await complete(client, endpoint, body)
    return decide_hermes(reply["choices"][0]["message"], "list_selected_documents")


PROBES: Final = {
    "cache_salt": probe_cache_salt,
    "priority": probe_priority,
    "thinking": probe_thinking,
    "hermes": probe_hermes,
}


async def run_probes(endpoint: Endpoint, names: Sequence[str] = tuple(PROBES)) -> dict[str, Any]:
    results: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=300) as client:
        for name in names:
            started = time.perf_counter()
            try:
                result = await PROBES[name](endpoint, client)
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                result = ProbeResult(
                    Verdict.INCONCLUSIVE, f"probe failed: {type(exc).__name__}", {}
                )
            results[name] = {
                **asdict(result),
                "verdict": result.verdict.value,
                "seconds": round(time.perf_counter() - started, 2),
            }
    return results
