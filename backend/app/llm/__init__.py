"""LLM gateway: the only module that talks to the vLLM servers (ADR-009, ADR-031)."""

from app.llm.errors import (
    LLMConfigError,
    LLMError,
    LLMRequestError,
    LLMSchemaError,
    LLMStreamInterrupted,
    LLMTimeout,
    LLMUnavailable,
    MissingCacheSalt,
)
from app.llm.gateway import Gateway, close_gateway, get_gateway, parse_guard_output
from app.llm.types import (
    ChatResult,
    GuardKind,
    GuardVerdict,
    Message,
    RequestPriority,
    StreamEnd,
    StreamEvent,
    TextDelta,
    Tool,
    ToolCall,
    Usage,
)

__all__ = [
    "ChatResult",
    "Gateway",
    "GuardKind",
    "GuardVerdict",
    "LLMConfigError",
    "LLMError",
    "LLMRequestError",
    "LLMSchemaError",
    "LLMStreamInterrupted",
    "LLMTimeout",
    "LLMUnavailable",
    "Message",
    "MissingCacheSalt",
    "RequestPriority",
    "StreamEnd",
    "StreamEvent",
    "TextDelta",
    "Tool",
    "ToolCall",
    "Usage",
    "close_gateway",
    "get_gateway",
    "parse_guard_output",
]
