import asyncio

import pytest

from app.core import readiness


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readiness, "_checks", {})


async def test_no_checks_means_ready() -> None:
    assert await readiness.failing_checks() == []


async def test_reports_only_failing_names() -> None:
    async def ok() -> None:
        return None

    async def false() -> bool:
        return False

    async def raises() -> None:
        raise ConnectionError("db at secret-host refused")

    async def slow() -> None:
        await asyncio.sleep(1)

    readiness.register_check("ok", ok)
    readiness.register_check("false", false)
    readiness.register_check("raises", raises)
    readiness.register_check("slow", slow)

    assert await readiness.failing_checks(timeout_s=0.05) == ["false", "raises", "slow"]


def test_duplicate_registration_rejected() -> None:
    async def ok() -> None:
        return None

    readiness.register_check("dup", ok)
    with pytest.raises(ValueError, match="dup"):
        readiness.register_check("dup", ok)
