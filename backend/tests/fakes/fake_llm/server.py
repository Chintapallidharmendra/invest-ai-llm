"""OpenAI-compatible fake LLM server (Story 1.6; ADR-037).

Implements what the gateway uses of vLLM's API: ``GET /health``, ``GET /v1/models`` and
``POST /v1/chat/completions`` (non-streaming and SSE streaming, ``tool_calls``, and
``response_format`` structured output). Replies come from the scenario registry in
:mod:`tests.fakes.fake_llm.scenarios`; every request is recorded.

:class:`FakeLLM` runs the app with uvicorn in a background thread on a free loopback
port, so both sync and async tests (and the ``openai`` SDK) talk to it over real HTTP.
"""

import asyncio
import itertools
import json
import logging
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from typing import Any, Final

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from tests.fakes.fake_llm.scenarios import (
    SCENARIO_HEADER,
    Error,
    Guard,
    Json,
    Raw,
    RecordedRequest,
    Registry,
    Reply,
    Scenario,
    Text,
    ToolCall,
    ToolCalls,
)

START_TIMEOUT_S: Final = 10.0

_ids = itertools.count(1)


class StreamInterrupted(Exception):  # noqa: N818 (a signal, not an error of the fake)
    """Raised inside a stream to drop the connection mid-response."""


class _DropInterruptLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        return not isinstance(exc, StreamInterrupted) and not any(
            isinstance(e, StreamInterrupted) for e in getattr(exc, "exceptions", ())
        )


logging.getLogger("uvicorn.error").addFilter(_DropInterruptLogs())


def _pieces(text: str, size: int) -> Iterator[str]:
    for start in range(0, len(text), max(size, 1)):
        yield text[start : start + size]


def _content(reply: Reply) -> str | None:
    match reply:
        case Text(content) | Raw(content):
            return content
        case Json(value):
            return json.dumps(value)
        case Guard():
            return reply.render()
        case ToolCalls(content=content):
            return content
        case _:
            return None


def _tool_calls(reply: Reply, request_no: int) -> list[dict[str, Any]]:
    if not isinstance(reply, ToolCalls):
        return []
    return [
        {
            "id": call.id or f"call_{request_no}_{index}",
            "type": "function",
            "function": {"name": call.name, "arguments": json.dumps(dict(call.arguments))},
        }
        for index, call in enumerate(reply.calls)
    ]


def _usage(body: Mapping[str, Any], completion: str) -> dict[str, int]:
    prompt = len(json.dumps(body.get("messages") or []).split())
    completion_tokens = len(completion.split())
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt + completion_tokens,
    }


def _completion(body: Mapping[str, Any], reply: Reply, request_no: int) -> dict[str, Any]:
    calls = _tool_calls(reply, request_no)
    content = _content(reply)
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return {
        "id": f"chatcmpl-fake-{request_no}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model"),
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if calls else "stop",
                "logprobs": None,
            }
        ],
        "usage": _usage(body, (content or "") + json.dumps(calls)),
    }


async def _stream(
    body: Mapping[str, Any], reply: Reply, scenario: Scenario | None, request_no: int
) -> AsyncIterator[str]:
    chunk_id, created, model = f"chatcmpl-fake-{request_no}", int(time.time()), body.get("model")
    chunk_size = scenario.chunk_size if scenario else 8
    chunk_delay = scenario.chunk_delay_s if scenario else 0.0
    interrupt_after = scenario.interrupt_after if scenario else None

    def event(payload: Mapping[str, Any]) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    def chunk(delta: Mapping[str, Any], finish: str | None = None) -> str:
        return event(
            {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
        )

    sent = 0

    async def pace() -> None:
        if chunk_delay:
            await asyncio.sleep(chunk_delay)
        if interrupt_after is not None and sent >= interrupt_after:
            raise StreamInterrupted

    yield chunk({"role": "assistant", "content": ""})
    content = _content(reply) or ""
    for piece in _pieces(content, chunk_size):
        await pace()
        yield chunk({"content": piece})
        sent += 1

    calls = _tool_calls(reply, request_no)
    for index, call in enumerate(calls):
        await pace()
        head = {"name": call["function"]["name"], "arguments": ""}
        yield chunk(
            {
                "tool_calls": [
                    {"index": index, "id": call["id"], "type": "function", "function": head}
                ]
            }
        )
        # Arguments arrive split across deltas, as vLLM's parser streams them.
        for piece in _pieces(call["function"]["arguments"], chunk_size):
            await pace()
            yield chunk({"tool_calls": [{"index": index, "function": {"arguments": piece}}]})
            sent += 1

    yield chunk({}, "tool_calls" if calls else "stop")
    options = body.get("stream_options") or {}
    if isinstance(options, dict) and options.get("include_usage"):
        yield event(
            {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [],
                "usage": _usage(body, content + json.dumps(calls)),
            }
        )
    yield "data: [DONE]\n\n"


def _error(status: int, message: str, kind: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": kind, "param": None, "code": status}},
        status_code=status,
    )


def create_fake_app(registry: Registry) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def record(request: Request, body: Mapping[str, Any] | None, scenario: str | None) -> None:
        registry.record(
            RecordedRequest(
                method=request.method,
                path=request.url.path,
                headers=dict(request.headers),
                body=body,
                scenario=scenario,
            )
        )

    @app.get("/health")
    async def health(request: Request) -> Response:
        record(request, None, None)
        return Response(status_code=200 if registry.healthy else 503)

    @app.get("/v1/models")
    async def models(request: Request) -> dict[str, Any]:
        record(request, None, None)
        return {
            "object": "list",
            "data": [
                {"id": m, "object": "model", "created": 0, "owned_by": "fake"}
                for m in registry.models
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        try:
            body = json.loads(await request.body())
        except ValueError:
            record(request, None, None)
            return _error(400, "invalid JSON body", "invalid_request_error")
        if not isinstance(body, dict):
            record(request, None, None)
            return _error(400, "body must be an object", "invalid_request_error")

        header = request.headers.get(SCENARIO_HEADER)
        try:
            scenario, reply = registry.resolve(header, body)
        except KeyError:
            record(request, body, None)
            return _error(404, f"unknown fake scenario: {header}", "not_found_error")
        record(request, body, scenario.name if scenario else None)

        if scenario is not None and scenario.delay_s:
            await asyncio.sleep(scenario.delay_s)
        if isinstance(reply, Error):
            return _error(reply.status, reply.message, reply.type)

        request_no = next(_ids)
        if body.get("stream"):
            return StreamingResponse(
                _stream(body, reply, scenario, request_no), media_type="text/event-stream"
            )
        return JSONResponse(_completion(body, reply, request_no))

    return app


class FakeLLM:
    """A running fake server plus its scenario API (the ``fake_llm`` fixture)."""

    def __init__(self) -> None:
        self.registry = Registry()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._port = 0

    # --- lifecycle -------------------------------------------------------------

    def start(self) -> "FakeLLM":
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        self._port = sock.getsockname()[1]
        config = uvicorn.Config(
            create_fake_app(self.registry),
            log_config=None,
            access_log=False,
            lifespan="off",
            timeout_graceful_shutdown=1,
        )
        server = uvicorn.Server(config)
        self._server = server
        self._thread = threading.Thread(
            target=server.run, kwargs={"sockets": [sock]}, name="fake-llm", daemon=True
        )
        self._thread.start()
        deadline = time.monotonic() + START_TIMEOUT_S
        while not server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("fake LLM server did not start")
            time.sleep(0.01)
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = self._thread = None

    # --- addresses -------------------------------------------------------------

    @property
    def url(self) -> str:
        """Server root (``/health`` lives here)."""
        return f"http://127.0.0.1:{self._port}"

    @property
    def base_url(self) -> str:
        """OpenAI base URL (``AsyncOpenAI(base_url=...)``)."""
        return f"{self.url}/v1"

    # --- scenario API ----------------------------------------------------------

    def add(
        self,
        name: str | None = None,
        *,
        reply: Reply | None = None,
        replies: Sequence[Reply] | None = None,
        model: str | None = None,
        pattern: str | None = None,
        delay_s: float = 0.0,
        chunk_delay_s: float = 0.0,
        chunk_size: int = 8,
        interrupt_after: int | None = None,
    ) -> Scenario:
        """Register a scenario. Give ``reply`` or ``replies`` (consumed in order)."""
        if (reply is None) == (replies is None):
            raise ValueError("pass exactly one of reply= or replies=")
        return self.registry.add(
            Scenario(
                name=name or f"scenario-{len(self.registry.scenarios) + 1}",
                replies=[reply] if reply is not None else list(replies or ()),
                model=model,
                pattern=pattern,
                delay_s=delay_s,
                chunk_delay_s=chunk_delay_s,
                chunk_size=chunk_size,
                interrupt_after=interrupt_after,
            )
        )

    @property
    def requests(self) -> list[RecordedRequest]:
        """Every request received, oldest first (including health and model listing)."""
        with self.registry._lock:
            return list(self.registry.requests)

    @property
    def chat_requests(self) -> list[RecordedRequest]:
        return [r for r in self.requests if r.path == "/v1/chat/completions"]

    @property
    def last_request(self) -> RecordedRequest:
        chats = self.chat_requests
        if not chats:
            raise AssertionError("the fake LLM received no chat completion requests")
        return chats[-1]

    @property
    def healthy(self) -> bool:
        return self.registry.healthy

    @healthy.setter
    def healthy(self, value: bool) -> None:
        self.registry.healthy = value

    @property
    def models(self) -> list[str]:
        return self.registry.models

    @models.setter
    def models(self, value: list[str]) -> None:
        self.registry.models = list(value)

    def reset(self) -> None:
        self.registry.reset()


__all__ = [
    "Error",
    "FakeLLM",
    "Guard",
    "Json",
    "Raw",
    "Scenario",
    "StreamInterrupted",
    "Text",
    "ToolCall",
    "ToolCalls",
    "create_fake_app",
]
