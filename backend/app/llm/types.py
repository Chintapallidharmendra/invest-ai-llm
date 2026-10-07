"""Gateway request and result types (no ``openai`` types leak out of ``app.llm``)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Literal

# OpenAI-format chat messages and tool definitions, as plain dicts.
Message = Mapping[str, Any]
Tool = Mapping[str, Any]

GuardKind = Literal["prompt", "response"]
Safety = Literal["safe", "unsafe", "controversial"]

PARSE_ERROR_CATEGORY = "parse_error"


class RequestPriority(IntEnum):
    """vLLM priority: lower values are scheduled first (ADR-029)."""

    CHAT = 0
    BACKGROUND = 10


@dataclass(frozen=True)
class GuardVerdict:
    safety: Safety
    categories: frozenset[str] = frozenset()
    refusal: bool | None = None  # response moderation only

    @property
    def is_safe(self) -> bool:
        """Only an explicit ``Safe`` counts; controversial and parse errors do not."""
        return self.safety == "safe"


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text, as the model produced it


@dataclass(frozen=True)
class ChatResult:
    content: str | None
    tool_calls: Sequence[ToolCall] = ()
    finish_reason: str | None = None
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class TextDelta:
    """A piece of streamed assistant text."""

    text: str


@dataclass(frozen=True)
class StreamEnd:
    """The last event of a completed stream (tool calls are assembled from deltas)."""

    finish_reason: str | None
    tool_calls: Sequence[ToolCall] = ()
    usage: Usage = field(default_factory=Usage)


StreamEvent = TextDelta | StreamEnd
