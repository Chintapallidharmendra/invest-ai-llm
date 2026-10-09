"""A minimal OpenAI-compatible chat client for the spike (httpx, no SDK).

Raw requests keep every vLLM-specific field visible: ``cache_salt``, ``priority`` and
``chat_template_kwargs`` (thinking off) go in the body exactly as the gateway sends
them (Story 1.5).
"""

import json
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

THINKING_OFF: dict[str, Any] = {"chat_template_kwargs": {"enable_thinking": False}}


@dataclass(frozen=True)
class Endpoint:
    base_url: str  # e.g. http://vllm-chat:8000/v1
    model: str = "chat"
    extra_body: Mapping[str, Any] = field(default_factory=lambda: dict(THINKING_OFF))

    @property
    def root(self) -> str:
        return self.base_url.rstrip("/").removesuffix("/v1")


def chat_body(
    endpoint: Endpoint,
    messages: Sequence[Mapping[str, Any]],
    *,
    max_tokens: int = 256,
    stream: bool = False,
    tools: Sequence[Mapping[str, Any]] | None = None,
    cache_salt: str | None = None,
    priority: int | None = None,
    temperature: float = 0.0,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": endpoint.model,
        "messages": list(messages),
        "max_tokens": max_tokens,
        "temperature": temperature,
        **endpoint.extra_body,
    }
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    if tools:
        body["tools"] = list(tools)
        body["tool_choice"] = "auto"
    if cache_salt is not None:
        body["cache_salt"] = cache_salt
    if priority is not None:
        body["priority"] = priority
    return body


@dataclass
class StreamEvent:
    """One parsed SSE chunk with its arrival time (``time.perf_counter``)."""

    at: float
    data: dict[str, Any]


async def stream_chat(
    client: httpx.AsyncClient, endpoint: Endpoint, body: Mapping[str, Any]
) -> AsyncIterator[StreamEvent]:
    """POST a streaming chat completion; yields parsed chunks as they arrive."""
    async with client.stream(
        "POST", f"{endpoint.base_url.rstrip('/')}/chat/completions", json=dict(body)
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                return
            yield StreamEvent(time.perf_counter(), json.loads(payload))


async def complete(
    client: httpx.AsyncClient, endpoint: Endpoint, body: Mapping[str, Any]
) -> dict[str, Any]:
    response = await client.post(
        f"{endpoint.base_url.rstrip('/')}/chat/completions", json=dict(body)
    )
    response.raise_for_status()
    result: dict[str, Any] = response.json()
    return result


async def metrics(client: httpx.AsyncClient, endpoint: Endpoint) -> dict[str, float]:
    """vLLM's Prometheus counters and gauges as ``{name{labels}: value}``; {} if absent."""
    try:
        response = await client.get(f"{endpoint.root}/metrics")
    except httpx.HTTPError:
        return {}
    if response.status_code != 200:  # noqa: PLR2004
        return {}
    return parse_prometheus(response.text)


def parse_prometheus(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, value = line.rpartition(" ")
        try:
            values[name.strip()] = float(value)
        except ValueError:
            continue
    return values


def metric_sum(values: Mapping[str, float], name: str) -> float:
    """Sum of a metric over all label sets (``name`` or ``name{...}``)."""
    return sum(v for k, v in values.items() if k == name or k.startswith(name + "{"))
