from datetime import timedelta

from app.core.ids import new_uuid7
from app.core.timeutil import utcnow


def test_new_uuid7_is_version_7_and_ordered() -> None:
    first, second = new_uuid7(), new_uuid7()
    assert first.version == 7
    assert first < second


def test_utcnow_is_aware_utc() -> None:
    assert utcnow().utcoffset() == timedelta(0)
