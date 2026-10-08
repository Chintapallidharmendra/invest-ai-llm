"""Shared helpers for the grounding tests. Unicode dashes and spaces are built with
``chr`` so the test sources stay ASCII-clean."""

import pytest

EN_DASH = chr(0x2013)
EM_DASH = chr(0x2014)
MINUS = chr(0x2212)
NBSP = chr(0xA0)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "perf: micro-benchmark with a latency/throughput bar")
