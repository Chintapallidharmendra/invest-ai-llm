"""AC #4 micro-benchmarks: detect p95 <= 20 ms on 2,000 characters; mask_batch >= 500
segments/s of ~1,000 characters. Deselect with ``-m "not perf"`` on a loaded machine."""

import statistics
import time

import pytest

from app.guardrails.identifiers.engine import IdentifierEngine

pytestmark = pytest.mark.perf

# Business prose with the usual numbers, amounts and contacts, plus a few identifiers.
_PARAGRAPH = (
    "Acme Industries Ltd (CIN L17110MH1973PLC019786, GSTIN 27ABCDE1234F1Z5) reported "
    "revenue of Rs 4,215.5 crore in FY25, up 12.4% YoY, with EBITDA of ₹812 cr and a "
    "margin of 19.3%. The board met on 2025-05-14; contact ir@acme.co.in or +91 22 6000 "
    "0000. ISIN INE002A01018 trades as NSE: ACME. The promoter's PAN is ABCPD1234E and "
    "proceeds go to a/c 50100123456789 (IFSC HDFC0001234). Order book stood at 18,450 "
    "units across 214 customers in 9 states; capex of ₹1,23,45,678 was approved. "
)


def _text(length: int) -> str:
    return (_PARAGRAPH * (length // len(_PARAGRAPH) + 1))[:length]


def test_detect_p95_on_a_2000_char_message(engine: IdentifierEngine) -> None:
    text = _text(2000)
    for _ in range(20):
        engine.detect(text)
    samples = []
    for _ in range(200):
        start = time.perf_counter()
        engine.detect(text)
        samples.append((time.perf_counter() - start) * 1000)
    p95 = statistics.quantiles(samples, n=20)[-1]
    assert engine.detect(text)  # the text does hold identifiers
    assert p95 <= 20.0, f"p95 {p95:.2f} ms"


def test_mask_batch_throughput(engine: IdentifierEngine) -> None:
    segments = [_text(1000 + i % 7) for i in range(600)]
    engine.mask_batch(segments[:20])
    start = time.perf_counter()
    results = engine.mask_batch(segments)
    rate = len(segments) / (time.perf_counter() - start)
    assert all(r.findings for r in results)
    assert rate >= 500, f"{rate:.0f} segments/s"
