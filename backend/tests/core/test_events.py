from typing import Any

import pytest

from app.core import events


async def test_emit_calls_subscribers_in_order() -> None:
    calls: list[tuple[str, Any]] = []

    async def first(**payload: Any) -> None:
        calls.append(("first", payload))

    async def second(**payload: Any) -> None:
        calls.append(("second", payload))

    unsubscribe_first = events.subscribe("test.happened", first)
    unsubscribe_second = events.subscribe("test.happened", second)
    try:
        await events.emit("test.happened", user_id="u1")
        await events.emit("test.other")
    finally:
        unsubscribe_first()
        unsubscribe_second()

    assert calls == [("first", {"user_id": "u1"}), ("second", {"user_id": "u1"})]


async def test_unsubscribe_and_errors_propagate() -> None:
    async def boom(**_: Any) -> None:
        raise RuntimeError("fail closed")

    unsubscribe = events.subscribe("test.boom", boom)
    with pytest.raises(RuntimeError):
        await events.emit("test.boom")
    unsubscribe()
    await events.emit("test.boom")
