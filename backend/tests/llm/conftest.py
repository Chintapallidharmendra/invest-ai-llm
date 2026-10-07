"""Fixtures for the LLM gateway tests (Story 1.5)."""

import base64
import hashlib
import hmac
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.llm.gateway import Gateway
from app.llm.profiles import LogicalModel, Profile, Timeouts, load_profiles
from app.llm.settings import LLMSettings
from tests.conftest import LLM_SALT_SECRET
from tests.core.conftest import logs
from tests.fakes.fake_llm import FakeLLM

__all__ = ["logs"]

CANARY = "CANARY-LLM-123"
USER_A = "0199a1b2-0000-7000-8000-00000000000a"
USER_B = "0199a1b2-0000-7000-8000-00000000000b"

_exporter = InMemorySpanExporter()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "gpu: needs the real vLLM servers (UAT VM)")
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_exporter))
    trace.set_tracer_provider(provider)


def expected_salt(subject: str) -> str:
    digest = hmac.new(LLM_SALT_SECRET.encode(), subject.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    _exporter.clear()
    yield _exporter
    _exporter.clear()


def llm_spans(exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    """The gateway's spans (the fake server's FastAPI emits its own as well)."""
    return [s for s in exporter.get_finished_spans() if s.name.startswith("llm.")]


def span_attributes(exporter: InMemorySpanExporter) -> list[dict[str, Any]]:
    return [dict(s.attributes or {}) for s in llm_spans(exporter)]


@pytest.fixture
def llm_settings(fake_llm_env: FakeLLM) -> LLMSettings:
    """Settings from the environment, pointing chat and guard at the fake."""
    return LLMSettings()


def profiles_with(
    settings: LLMSettings, name: LogicalModel, **changes: Any
) -> dict[LogicalModel, Profile]:
    profiles = load_profiles(settings.profiles_path, settings.base_urls)
    if "timeouts" in changes and isinstance(changes["timeouts"], dict):
        changes["timeouts"] = Timeouts(**changes["timeouts"])
    profiles[name] = profiles[name].model_copy(update=changes)
    return profiles


@pytest.fixture
async def gateway(llm_settings: LLMSettings) -> AsyncIterator[Gateway]:
    async with Gateway(llm_settings) as gw:
        yield gw
