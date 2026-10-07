"""The LLM gateway: the only code that talks to the model servers (ADR-009).

Every call:

- needs ``salt_subject`` (a user ID; background jobs pass the job owner). The gateway
  sends ``cache_salt`` derived from it (ADR-031). Without it, :class:`MissingCacheSalt`
  is raised before anything is sent. Only :meth:`Gateway.health` is exempt.
- needs a :class:`RequestPriority`. It is sent as ``priority`` when the profile says the
  server supports it (ADR-029).
- uses the profile's server, served model name, ``extra_body`` and limits, so models are
  swapped by configuration (NFR-015).
- has deadlines: connect 2 s; guard 3 s total; chat streams 10 s to the first token and
  60 s overall (non-streaming chat calls have only the 60 s total, as the first token is
  not observable). Connection errors are retried once; timeouts, HTTP errors and bad
  content never are.
- emits one span and one log line with allow-listed, content-free attributes only.
"""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from types import TracebackType
from typing import Any, Final, Self, cast
from uuid import UUID

import httpx
import openai
from openai import AsyncOpenAI
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel, ValidationError

from app.core import obs
from app.llm.errors import (
    LLMConfigError,
    LLMError,
    LLMRequestError,
    LLMSchemaError,
    LLMStreamInterrupted,
    LLMTimeout,
    LLMUnavailable,
)
from app.llm.profiles import LogicalModel, Profile, load_profiles
from app.llm.salt import CacheSalter, subject_key
from app.llm.settings import LLMSettings
from app.llm.types import (
    PARSE_ERROR_CATEGORY,
    ChatResult,
    GuardKind,
    GuardVerdict,
    Message,
    RequestPriority,
    Safety,
    StreamEnd,
    StreamEvent,
    TextDelta,
    Tool,
    ToolCall,
    Usage,
)

HEALTH_TIMEOUT_S: Final = 2.0
# vLLM accepts any key when started without --api-key; this is not a secret.
UNUSED_API_KEY: Final = "EMPTY"
SPAN_ATTRIBUTES: Final = ("llm_model", "llm_served_model", "llm_priority", "llm_outcome")

obs.allow_span_attributes(*SPAN_ATTRIBUTES)
_log = obs.get_logger("llm.gateway")


# --- Errors and telemetry ------------------------------------------------------------


def _as_llm_error(exc: BaseException, *, mid_stream: bool = False) -> LLMError | None:
    """Map SDK failures to typed errors. Messages carry no server text, which can echo
    prompt content. (The SDK wraps its own transport errors, including mid-stream ones,
    as ``APIConnectionError``.)"""
    if isinstance(exc, LLMError):
        return exc
    if isinstance(exc, TimeoutError | openai.APITimeoutError):
        return LLMTimeout("model server deadline exceeded")
    if mid_stream and isinstance(exc, openai.APIError):
        return LLMStreamInterrupted("stream broke off before it finished")
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        retryable = status >= 500 or status == 429  # noqa: PLR2004
        return (
            LLMUnavailable(f"model server returned HTTP {status}")
            if retryable
            else LLMRequestError(f"model server rejected the request: HTTP {status}")
        )
    if isinstance(exc, openai.APIConnectionError):
        return LLMUnavailable("model server unreachable")
    return None


class _Call:
    """Telemetry for one call. The span is not made current, so it is safe to keep open
    across a stream's yields."""

    def __init__(
        self, operation: str, logical: LogicalModel, profile: Profile, priority: RequestPriority
    ) -> None:
        self.usage = Usage()
        self._operation = operation
        self._start = time.perf_counter()
        self._span = obs.get_tracer("app.llm").start_span(
            f"llm.{operation}",
            attributes=obs.filter_span_attributes(
                {
                    "llm_model": logical,
                    "llm_served_model": profile.served_model,
                    "llm_priority": int(priority),
                }
            ),
        )
        self._done = False

    def finish(self, outcome: str, error: LLMError | None = None) -> None:
        if self._done:
            return
        self._done = True
        latency_ms = round((time.perf_counter() - self._start) * 1000)
        attributes: dict[str, Any] = {
            "llm_outcome": outcome,
            "latency_ms": latency_ms,
            "prompt_token_count": self.usage.prompt_tokens,
            "completion_token_count": self.usage.completion_tokens,
        }
        if error is not None:
            attributes["error_code"] = error.code
            self._span.set_status(Status(StatusCode.ERROR))
        self._span.set_attributes(obs.filter_span_attributes(attributes))
        self._span.end()
        fields: dict[str, Any] = {
            "latency_ms": latency_ms,
            "prompt_token_count": self.usage.prompt_tokens,
            "completion_token_count": self.usage.completion_tokens,
        }
        if error is None:
            _log.info(f"llm.{self._operation}", **fields)
        else:
            _log.warning(f"llm.{self._operation}", error_code=error.code, **fields)


def _usage(raw: Any) -> Usage:
    if raw is None:
        return Usage()
    return Usage(raw.prompt_tokens or 0, raw.completion_tokens or 0)


def _chat_result(response: Any) -> ChatResult:
    choice = response.choices[0]
    calls = [
        ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments or "")
        for c in choice.message.tool_calls or []
        if getattr(c, "function", None) is not None
    ]
    return ChatResult(
        content=choice.message.content,
        tool_calls=calls,
        finish_reason=choice.finish_reason,
        usage=_usage(response.usage),
    )


# --- Guard output ---------------------------------------------------------------------

_SAFETY_RE: Final = re.compile(r"^\s*Safety:\s*(Safe|Unsafe|Controversial)\s*$", re.I | re.M)
_CATEGORIES_RE: Final = re.compile(r"^\s*Categories:\s*(.*?)\s*$", re.I | re.M)
_REFUSAL_RE: Final = re.compile(r"^\s*Refusal:\s*(Yes|No)\s*$", re.I | re.M)


def parse_guard_output(text: str | None) -> GuardVerdict:
    """Parse Qwen3Guard-Gen output (``Safety:`` / ``Categories:`` [/ ``Refusal:``]).

    Anything unparseable is ``controversial`` with category ``parse_error``, which
    callers treat as not safe (ADR-024).
    """
    safety = _SAFETY_RE.search(text or "")
    if safety is None:
        return GuardVerdict("controversial", frozenset({PARSE_ERROR_CATEGORY}))
    categories_match = _CATEGORIES_RE.search(text or "")
    categories = frozenset(
        c.strip()
        for c in (categories_match.group(1) if categories_match else "").split(",")
        if c.strip() and c.strip().lower() != "none"
    )
    refusal = _REFUSAL_RE.search(text or "")
    return GuardVerdict(
        cast(Safety, safety.group(1).lower()),
        categories,
        None if refusal is None else refusal.group(1).lower() == "yes",
    )


class _StreamDeadlines:
    """First-token deadline until the first token arrives, then the total deadline."""

    def __init__(self, profile: Profile, start: float) -> None:
        timeouts = profile.timeouts
        self.total = start + timeouts.total_s
        self.first_token = min(start + (timeouts.first_token_s or timeouts.total_s), self.total)

    def next(self, started: bool) -> float:
        return self.total if started else self.first_token


class _StreamState:
    """Accumulates a chat stream: text pieces out, tool-call deltas assembled."""

    def __init__(self) -> None:
        self.started = False
        self.finish_reason: str | None = None
        self.usage = Usage()
        self._tools: dict[int, dict[str, str]] = {}

    def consume(self, chunk: Any) -> list[str]:
        if getattr(chunk, "usage", None) is not None:
            self.usage = _usage(chunk.usage)
        texts: list[str] = []
        for choice in chunk.choices:
            delta = choice.delta
            for part in delta.tool_calls or []:
                self.started = True
                entry = self._tools.setdefault(part.index, {"id": "", "name": "", "args": ""})
                entry["id"] = part.id or entry["id"]
                if part.function is not None:
                    entry["name"] += part.function.name or ""
                    entry["args"] += part.function.arguments or ""
            if delta.content:
                self.started = True
                texts.append(delta.content)
            self.finish_reason = choice.finish_reason or self.finish_reason
        return texts

    def end(self) -> StreamEnd:
        if self.finish_reason is None:
            raise LLMStreamInterrupted("stream ended without a finish reason")
        return StreamEnd(
            finish_reason=self.finish_reason,
            tool_calls=[
                ToolCall(id=t["id"], name=t["name"], arguments=t["args"])
                for _, t in sorted(self._tools.items())
            ],
            usage=self.usage,
        )


# --- Gateway ----------------------------------------------------------------------------


class Gateway:
    def __init__(
        self, settings: LLMSettings, profiles: Mapping[LogicalModel, Profile] | None = None
    ) -> None:
        self._profiles = dict(profiles or load_profiles(settings.profiles_path, settings.base_urls))
        unsalted = sorted(n for n, p in self._profiles.items() if not p.supports_cache_salt)
        if unsalted and not settings.prefix_caching_disabled:
            raise LLMConfigError(
                f"profiles {unsalted} do not support cache_salt: disable prefix caching on "
                "the server and set APP_LLM_PREFIX_CACHING_DISABLED=true (ADR-031)"
            )
        self._salter = CacheSalter(settings.cache_salt_secret)
        self._clients = {
            name: AsyncOpenAI(
                base_url=p.base_url,
                api_key=UNUSED_API_KEY,
                max_retries=0,  # the gateway retries connection errors itself, once
                timeout=openai.Timeout(p.timeouts.total_s, connect=p.timeouts.connect_s),
            )
            for name, p in self._profiles.items()
        }
        self._health_http = httpx.AsyncClient(timeout=HEALTH_TIMEOUT_S)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, _: type[BaseException] | None, __: BaseException | None, ___: TracebackType | None
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.close()
        await self._health_http.aclose()

    # --- configuration shared with the PydanticAI factory ---------------------------

    def profile(self, name: LogicalModel) -> Profile:
        return self._profiles[name]

    def openai_client(self, name: LogicalModel) -> AsyncOpenAI:
        return self._clients[name]

    def extra_body(
        self, name: LogicalModel, salt_subject: str | UUID | None, priority: RequestPriority
    ) -> dict[str, Any]:
        """Profile ``extra_body`` plus ``cache_salt`` and (if supported) ``priority``."""
        subject_key(salt_subject)  # MissingCacheSalt even if the profile sends no salt
        profile = self._profiles[name]
        body = json.loads(json.dumps(profile.extra_body))  # deep copy
        if profile.supports_cache_salt:
            body["cache_salt"] = self._salter.salt(salt_subject)
        if profile.supports_priority:
            body["priority"] = int(priority)
        return cast(dict[str, Any], body)

    def _params(
        self,
        name: LogicalModel,
        messages: Sequence[Message],
        *,
        salt_subject: str | UUID | None,
        priority: RequestPriority,
        max_tokens: int | None = None,
        temperature: float | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        profile = self._profiles[name]
        params: dict[str, Any] = {
            "model": profile.served_model,
            "messages": [dict(m) for m in messages],
            "max_tokens": max_tokens or profile.max_tokens,
            "extra_body": self.extra_body(name, salt_subject, priority),
        }
        temperature = temperature if temperature is not None else profile.temperature
        if temperature is not None:
            params["temperature"] = temperature
        params.update({k: v for k, v in fields.items() if v is not None})
        return params

    # --- transport ---------------------------------------------------------------------

    async def _create(self, name: LogicalModel, params: Mapping[str, Any]) -> Any:
        """One request, retried once on a connection error (never on timeouts)."""
        create = cast(Any, self._clients[name].chat.completions.create)
        try:
            return await create(**params)
        except openai.APITimeoutError:
            raise
        except openai.APIConnectionError:
            _log.info("llm.retry_connection")
            return await create(**params)

    async def _run[R](
        self,
        operation: str,
        name: LogicalModel,
        priority: RequestPriority,
        work: Callable[[_Call], Awaitable[R]],
    ) -> R:
        profile = self._profiles[name]
        call = _Call(operation, name, profile, priority)
        try:
            async with asyncio.timeout(profile.timeouts.total_s):
                result = await work(call)
        except asyncio.CancelledError:
            call.finish("cancelled")
            raise
        except Exception as exc:
            error = _as_llm_error(exc)
            call.finish(error.code if error else "error", error)
            if error is None or error is exc:
                raise
            raise error from None
        call.finish("ok")
        return result

    # --- public API --------------------------------------------------------------------

    async def chat_completion(
        self,
        messages: Sequence[Message],
        *,
        salt_subject: str | UUID | None = None,
        priority: RequestPriority,
        tools: Sequence[Tool] | None = None,
        tool_choice: str | Mapping[str, Any] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResult:
        params = self._params(
            "chat",
            messages,
            salt_subject=salt_subject,
            priority=priority,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=list(tools) if tools else None,
            tool_choice=tool_choice,
        )

        async def work(call: _Call) -> ChatResult:
            result = _chat_result(await self._create("chat", params))
            call.usage = result.usage
            return result

        return await self._run("chat_completion", "chat", priority, work)

    async def structured[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        salt_subject: str | UUID | None = None,
        priority: RequestPriority,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> T:
        """JSON-schema-guided output (OpenAI ``response_format: json_schema``), validated
        into ``schema``. Invalid output raises :class:`LLMSchemaError`."""
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": schema.model_json_schema(),
                "strict": True,
            },
        }
        params = self._params(
            "chat",
            messages,
            salt_subject=salt_subject,
            priority=priority,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
        )

        async def work(call: _Call) -> T:
            result = _chat_result(await self._create("chat", params))
            call.usage = result.usage
            if not result.content:
                raise LLMSchemaError("structured output was empty")
            try:
                return schema.model_validate_json(result.content)
            except ValidationError:
                # The validation error quotes the output; never chain it.
                raise LLMSchemaError("structured output did not match the schema") from None

        return await self._run("structured", "chat", priority, work)

    async def guard_classify(
        self,
        text: str,
        kind: GuardKind = "prompt",
        *,
        salt_subject: str | UUID | None = None,
        priority: RequestPriority,
        prompt: str | None = None,
    ) -> GuardVerdict:
        """Classify a user prompt, or a model response (optionally with the ``prompt``
        that produced it, as Qwen3Guard-Gen's response moderation expects)."""
        if kind == "prompt":
            messages: list[Message] = [{"role": "user", "content": text}]
        else:
            messages = [
                {"role": "user", "content": prompt or ""},
                {"role": "assistant", "content": text},
            ]
        params = self._params("guard", messages, salt_subject=salt_subject, priority=priority)

        async def work(call: _Call) -> GuardVerdict:
            result = _chat_result(await self._create("guard", params))
            call.usage = result.usage
            verdict = parse_guard_output(result.content)
            if PARSE_ERROR_CATEGORY in verdict.categories:
                _log.warning("llm.guard_parse_error")
            return verdict

        return await self._run("guard_classify", "guard", priority, work)

    def stream_chat(
        self,
        messages: Sequence[Message],
        *,
        salt_subject: str | UUID | None = None,
        priority: RequestPriority,
        tools: Sequence[Tool] | None = None,
        tool_choice: str | Mapping[str, Any] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream :class:`TextDelta` events, then one :class:`StreamEnd`.

        Validation (salt) happens on the call, before iteration. A stream that breaks
        off raises :class:`LLMStreamInterrupted`; the caller discards partial text.
        """
        params = self._params(
            "chat",
            messages,
            salt_subject=salt_subject,
            priority=priority,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=list(tools) if tools else None,
            tool_choice=tool_choice,
            stream=True,
            stream_options={"include_usage": True},
        )
        return self._stream(params, priority)

    async def _stream(
        self, params: Mapping[str, Any], priority: RequestPriority
    ) -> AsyncIterator[StreamEvent]:
        profile = self._profiles["chat"]
        call = _Call("stream_chat", "chat", profile, priority)
        deadlines = _StreamDeadlines(profile, asyncio.get_running_loop().time())
        state = _StreamState()
        stream: Any = None
        try:
            async with asyncio.timeout_at(deadlines.first_token):
                stream = await self._create("chat", params)
            chunks = aiter(stream)
            while True:
                try:
                    async with asyncio.timeout_at(deadlines.next(state.started)):
                        chunk = await anext(chunks)
                except StopAsyncIteration:
                    break
                for text in state.consume(chunk):
                    yield TextDelta(text)
            call.usage = state.usage
            yield state.end()
        except (GeneratorExit, asyncio.CancelledError):
            call.finish("cancelled")
            raise
        except Exception as exc:
            error = _as_llm_error(exc, mid_stream=stream is not None)
            call.finish(error.code if error else "error", error)
            if error is None or error is exc:
                raise
            raise error from None
        else:
            call.finish("ok")
        finally:
            if stream is not None:
                await stream.close()

    async def health(self, name: LogicalModel) -> bool:
        """``GET /health`` on the profile's server (no salt: carries no content)."""
        root = self._profiles[name].base_url.rstrip("/").removesuffix("/v1")
        try:
            response = await self._health_http.get(f"{root}/health")
        except httpx.HTTPError:
            return False
        return response.status_code == 200  # noqa: PLR2004


_gateway: Gateway | None = None


def get_gateway() -> Gateway:
    """The process-wide gateway, built from :class:`LLMSettings` on first use."""
    global _gateway  # noqa: PLW0603 (lazy process-wide client pool)
    if _gateway is None:
        _gateway = Gateway(LLMSettings())
    return _gateway


async def close_gateway() -> None:
    global _gateway  # noqa: PLW0603
    if _gateway is not None:
        await _gateway.aclose()
    _gateway = None
