import logging

import pytest
import structlog

from app.core import obs
from tests.core.conftest import LogCapture


@pytest.fixture
def scrubbers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(obs, "_scrubbers", [])


def test_disallowed_fields_are_dropped(logs: LogCapture) -> None:
    obs.get_logger("chat").info(
        "chat.answered", prompt="secret prompt", latency_ms=12.5, chunk_count=3, user_id="u1"
    )
    (event,) = logs.events
    assert event["event"] == "chat.answered"
    assert event["module"] == "chat"
    assert event["latency_ms"] == 12.5
    assert event["chunk_count"] == 3
    assert event["user_id"] == "u1"
    assert event["level"] == "info"
    assert "timestamp" in event
    assert "prompt" not in event
    assert "secret prompt" not in logs.text


def test_exception_logs_type_and_location_only(logs: LogCapture) -> None:
    def parse() -> None:
        raise ValueError("Revenue of Project Falcon was 1,234 crore")

    try:
        parse()
    except ValueError:
        obs.get_logger("documents").exception("documents.parse_failed")

    (event,) = logs.events
    assert event["exc_type"] == "ValueError"
    assert event["exc_location"].startswith("tests.core.test_obs:parse:")
    assert "Falcon" not in logs.text
    assert "Traceback" not in logs.text


def test_exc_info_object_is_summarised(logs: LogCapture) -> None:
    obs.get_logger("x").error("x.failed", exc_info=KeyError("client name"))
    (event,) = logs.events
    assert event["exc_type"] == "KeyError"
    assert event["exc_location"] == "unknown"
    assert "client name" not in logs.text


def test_registered_scrubber_runs(logs: LogCapture, scrubbers: None) -> None:
    @obs.register_scrubber
    def mask_pan(event_dict: structlog.typing.EventDict) -> structlog.typing.EventDict:
        return {k: ("[PAN]" if v == "ABCDE1234F" else v) for k, v in event_dict.items()}

    obs.get_logger("x").info("x.seen", user_id="ABCDE1234F")
    (event,) = logs.events
    assert event["user_id"] == "[PAN]"


def test_stdlib_messages_are_not_output(logs: LogCapture) -> None:
    logging.getLogger("some.library").warning("query failed for %s", "secret value")
    (event,) = logs.events
    assert event == {
        "event": "stdlib_log",
        "module": "some.library",
        "level": "warning",
        "timestamp": event["timestamp"],
    }


def test_context_fields_are_merged(logs: LogCapture) -> None:
    structlog.contextvars.bind_contextvars(correlation_id="c1", prompt="nope")
    try:
        obs.get_logger("x").info("x.done")
        assert obs.current_correlation_id() == "c1"
    finally:
        structlog.contextvars.clear_contextvars()
    (event,) = logs.events
    assert event["correlation_id"] == "c1"
    assert "prompt" not in event


def test_span_attributes_are_allow_listed() -> None:
    attrs = obs.filter_span_attributes({"user_id": "u1", "chunk_count": 2, "prompt": "secret"})
    assert attrs == {"user_id": "u1", "chunk_count": 2}
    with obs.span("test.span", user_id="u1", prompt="secret"):
        pass
