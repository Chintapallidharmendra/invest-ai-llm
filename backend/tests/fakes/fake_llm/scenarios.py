"""Scenario registry and request recorder for the fake LLM (Story 1.6).

A scenario is matched per request, in this order:

1. the ``X-Fake-Scenario`` header names it (unknown names are an HTTP 404);
2. otherwise the most recently added scenario whose criteria all hold: ``model``
   equals the request's model and/or ``pattern`` (a regex) matches the last user
   message. A scenario with neither criterion only matches by header.

Without a match the fake answers with :data:`DEFAULT_TEXT`, or ``Safety: Safe`` for the
guard model.

Each matched request consumes the scenario's next reply; the last reply repeats. A
scenario with replies ``[ToolCalls(...), Text("done")]`` therefore emits the tool calls
first and the text for every later request (the turn after the tool results).
"""

import json
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

DEFAULT_TEXT: Final = "This is the fake LLM."
GUARD_MODEL: Final = "guard"
SCENARIO_HEADER: Final = "x-fake-scenario"


@dataclass(frozen=True)
class Text:
    """A plain assistant message."""

    content: str


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: Mapping[str, Any]
    id: str | None = None


@dataclass(frozen=True)
class ToolCalls:
    """An assistant message with OpenAI ``tool_calls`` (finish_reason ``tool_calls``)."""

    calls: Sequence[ToolCall]
    content: str | None = None


@dataclass(frozen=True)
class Json:
    """Structured output: ``value`` serialised as the message content.

    The fake does not validate ``value`` against the request's schema, so a scenario can
    deliberately return invalid output.
    """

    value: Any


@dataclass(frozen=True)
class Guard:
    """Qwen3Guard-Gen style verdict text."""

    safety: str = "Safe"  # Safe | Unsafe | Controversial
    categories: Sequence[str] = ()
    refusal: str | None = None  # response moderation adds "Refusal: Yes|No"

    def render(self) -> str:
        lines = [f"Safety: {self.safety}", f"Categories: {', '.join(self.categories) or 'None'}"]
        if self.refusal is not None:
            lines.append(f"Refusal: {self.refusal}")
        return "\n".join(lines)


@dataclass(frozen=True)
class Raw:
    """Arbitrary message content (e.g. unparseable guard output)."""

    content: str


@dataclass(frozen=True)
class Error:
    """An HTTP error response in the OpenAI error shape."""

    status: int = 500
    message: str = "fake error"
    type: str = "server_error"


Reply = Text | ToolCalls | Json | Guard | Raw | Error


@dataclass
class Scenario:
    """A scripted behaviour.

    ``delay_s`` waits before the response starts (headers included), so it can trip
    connect/first-token timeouts. ``chunk_delay_s`` waits between streamed chunks.
    ``interrupt_after`` drops the connection after that many streamed content chunks.
    """

    name: str
    replies: Sequence[Reply]
    model: str | None = None
    pattern: str | None = None
    delay_s: float = 0.0
    chunk_delay_s: float = 0.0
    chunk_size: int = 8
    interrupt_after: int | None = None
    calls: int = 0

    def __post_init__(self) -> None:
        if not self.replies:
            raise ValueError("a scenario needs at least one reply")
        self._regex = re.compile(self.pattern) if self.pattern is not None else None

    def matches(self, model: str | None, last_user: str) -> bool:
        if self.model is None and self._regex is None:
            return False
        if self.model is not None and self.model != model:
            return False
        return self._regex is None or self._regex.search(last_user) is not None

    def next_reply(self) -> Reply:
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return reply


@dataclass(frozen=True)
class RecordedRequest:
    """One request as the fake received it. ``body`` is the full JSON body, so fields the
    SDK sends via ``extra_body`` (``cache_salt``, ``priority``, ``chat_template_kwargs``)
    appear at the top level, exactly as vLLM sees them."""

    method: str
    path: str
    headers: Mapping[str, str]
    body: Mapping[str, Any] | None
    scenario: str | None = None

    def _get(self, key: str) -> Any:
        return None if self.body is None else self.body.get(key)

    @property
    def model(self) -> str | None:
        value = self._get("model")
        return value if isinstance(value, str) else None

    @property
    def cache_salt(self) -> str | None:
        value = self._get("cache_salt")
        return value if isinstance(value, str) else None

    @property
    def priority(self) -> int | None:
        value = self._get("priority")
        return value if isinstance(value, int) else None

    @property
    def messages(self) -> list[dict[str, Any]]:
        value = self._get("messages")
        return value if isinstance(value, list) else []

    def __contains__(self, text: str) -> bool:
        """``"CANARY" in request``: does the text occur anywhere in the body?"""
        return text in json.dumps(self.body)


def last_user_message(body: Mapping[str, Any]) -> str:
    for message in reversed(body.get("messages") or []):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):  # content parts
                return " ".join(
                    p.get("text", "") for p in content if isinstance(p, dict) and "text" in p
                )
    return ""


@dataclass
class Registry:
    """Thread-safe: the server runs in its own thread, tests read from theirs."""

    scenarios: list[Scenario] = field(default_factory=list)
    requests: list[RecordedRequest] = field(default_factory=list)
    models: list[str] = field(default_factory=lambda: ["chat", GUARD_MODEL])
    healthy: bool = True
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, scenario: Scenario) -> Scenario:
        with self._lock:
            self.scenarios.append(scenario)
        return scenario

    def reset(self) -> None:
        with self._lock:
            self.scenarios.clear()
            self.requests.clear()
            self.models = ["chat", GUARD_MODEL]
            self.healthy = True

    def record(self, request: RecordedRequest) -> None:
        with self._lock:
            self.requests.append(request)

    def resolve(self, header: str | None, body: Mapping[str, Any]) -> tuple[Scenario | None, Reply]:
        """Pick the scenario and consume its next reply. Raises ``KeyError`` for an
        unknown header name."""
        model = body.get("model")
        model = model if isinstance(model, str) else None
        with self._lock:
            if header:
                scenario = next((s for s in reversed(self.scenarios) if s.name == header), None)
                if scenario is None:
                    raise KeyError(header)
            else:
                last_user = last_user_message(body)
                scenario = next(
                    (s for s in reversed(self.scenarios) if s.matches(model, last_user)), None
                )
            if scenario is not None:
                return scenario, scenario.next_reply()
        return None, Guard() if model == GUARD_MODEL else Text(DEFAULT_TEXT)
