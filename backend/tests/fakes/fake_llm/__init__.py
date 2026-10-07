"""Scriptable OpenAI-compatible fake LLM (Story 1.6). See ``server.py``."""

from tests.fakes.fake_llm.scenarios import (
    DEFAULT_TEXT,
    SCENARIO_HEADER,
    Error,
    Guard,
    Json,
    Raw,
    RecordedRequest,
    Scenario,
    Text,
    ToolCall,
    ToolCalls,
)
from tests.fakes.fake_llm.server import FakeLLM, StreamInterrupted, create_fake_app

__all__ = [
    "DEFAULT_TEXT",
    "SCENARIO_HEADER",
    "Error",
    "FakeLLM",
    "Guard",
    "Json",
    "Raw",
    "RecordedRequest",
    "Scenario",
    "StreamInterrupted",
    "Text",
    "ToolCall",
    "ToolCalls",
    "create_fake_app",
]
