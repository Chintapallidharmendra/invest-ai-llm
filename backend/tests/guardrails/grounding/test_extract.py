"""AC #1: extraction, table-driven. Each case lists every figure expected in the text as
(raw, value, unit) or (raw, value, unit, kind)."""

from decimal import Decimal

import pytest

from app.guardrails.grounding import FigureKind as K
from app.guardrails.grounding import Unit as U  # noqa: N817
from app.guardrails.grounding import extract_figures
from tests.guardrails.grounding.conftest import EM_DASH, EN_DASH, MINUS, NBSP

Expected = tuple[str, int | str, U] | tuple[str, int | str, U, K]

CASES: list[tuple[str, list[Expected]]] = [
    # Currencies and scale words
    ("₹4,215 crore", [("₹4,215 crore", 42_150_000_000, U.INR)]),
    ("Rs. 4,215.37 cr", [("Rs. 4,215.37 cr", 42_153_700_000, U.INR)]),
    ("Rs 50 lakh", [("Rs 50 lakh", 5_000_000, U.INR)]),
    ("INR 12 lacs", [("INR 12 lacs", 1_200_000, U.INR)]),
    ("₹5 lakh crore", [("₹5 lakh crore", 5 * 10**12, U.INR)]),
    ("$5m", [("$5m", 5_000_000, U.USD)]),
    ("US$ 505 mn", [("US$ 505 mn", 505_000_000, U.USD)]),
    ("USD 1.2bn", [("USD 1.2bn", 1_200_000_000, U.USD)]),
    ("$1.2 billion", [("$1.2 billion", 1_200_000_000, U.USD)]),
    ("€3.2 billion", [("€3.2 billion", 3_200_000_000, U.EUR)]),
    ("EUR 450k", [("EUR 450k", 450_000, U.EUR)]),
    ("₹10 thousand", [("₹10 thousand", 10_000, U.INR)]),
    ("5 crore rupees", [("5 crore rupees", 50_000_000, U.INR)]),
    ("120 million dollars", [("120 million dollars", 120_000_000, U.USD)]),
    ("4,215 crore", [("4,215 crore", 42_150_000_000, U.PLAIN)]),
    ("1,200m", [("1,200m", 1_200_000_000, U.PLAIN)]),
    ("$2 tn", [("$2 tn", 2 * 10**12, U.USD)]),
    ("Rs.500", [("Rs.500", 500, U.INR)]),
    ("₹4,215cr", [("₹4,215cr", 42_150_000_000, U.INR)]),
    ("$5mm", [("$5mm", 5_000_000, U.USD)]),
    (f"₹{NBSP}4,215{NBSP}crore", [(f"₹{NBSP}4,215{NBSP}crore", 42_150_000_000, U.INR)]),
    # Indian and international grouping
    ("1,00,000", [("1,00,000", 100_000, U.COUNT)]),
    ("100,000", [("100,000", 100_000, U.COUNT)]),
    ("12,34,56,789", [("12,34,56,789", 123_456_789, U.COUNT)]),
    ("1,234,567.89", [("1,234,567.89", "1234567.89", U.PLAIN)]),
    ("₹1,23,45,678", [("₹1,23,45,678", 12_345_678, U.INR)]),
    ("0.75", [("0.75", "0.75", U.PLAIN)]),
    ("1,2,3", [("1", 1, U.COUNT), ("2", 2, U.COUNT), ("3", 3, U.COUNT)]),
    # Percentages, basis points, multiples
    ("12.5%", [("12.5%", "12.5", U.PCT)]),
    ("12.5 per cent", [("12.5 per cent", "12.5", U.PCT)]),
    ("8 percent", [("8 percent", 8, U.PCT)]),
    ("12 pct", [("12 pct", 12, U.PCT)]),
    ("25 bps", [("25 bps", 25, U.BPS)]),
    ("150 basis points", [("150 basis points", 150, U.BPS)]),
    ("40bp", [("40bp", 40, U.BPS)]),
    ("5.2x", [("5.2x", "5.2", U.MULTIPLE)]),
    ("2.5 times", [("2.5 times", "2.5", U.MULTIPLE)]),
    # Negatives
    ("-5", [("-5", -5, U.PLAIN)]),
    ("a loss of (5)", [("(5)", -5, U.PLAIN)]),
    ("(1,234)", [("(1,234)", -1234, U.PLAIN)]),
    ("-₹120 cr", [("-₹120 cr", -1_200_000_000, U.INR)]),
    (f"{MINUS}3.5%", [(f"{MINUS}3.5%", "-3.5", U.PCT)]),
    ("revenue (₹5 cr)", [("₹5 cr", 50_000_000, U.INR)]),
    # Ranges: each end, sharing the scale and unit written after the range
    (f"10{EN_DASH}12%", [("10", 10, U.PCT), ("12%", 12, U.PCT)]),
    (f"10{EM_DASH}12%", [("10", 10, U.PCT), ("12%", 12, U.PCT)]),
    ("10-12%", [("10", 10, U.PCT), ("12%", 12, U.PCT)]),
    ("₹10-12 cr", [("10", 100_000_000, U.INR), ("12 cr", 120_000_000, U.INR)]),
    ("5 to 6x", [("5", 5, U.MULTIPLE), ("6x", 6, U.MULTIPLE)]),
    ("3-4 deals", [("3", 3, U.COUNT), ("4", 4, U.COUNT)]),
    # Dates (ISO in value: YYYYMMDD, 00 for parts not written)
    ("06-Oct-2026", [("06-Oct-2026", 20261006, U.DATE, K.DATE)]),
    ("31 March 2025", [("31 March 2025", 20250331, U.DATE, K.DATE)]),
    ("March 31, 2025", [("March 31, 2025", 20250331, U.DATE, K.DATE)]),
    ("2025-03-31", [("2025-03-31", 20250331, U.DATE, K.DATE)]),
    ("31/03/2025", [("31/03/2025", 20250331, U.DATE, K.DATE)]),
    ("31.03.25", [("31.03.25", 20250331, U.DATE, K.DATE)]),
    ("6th Oct 2026", [("6th Oct 2026", 20261006, U.DATE, K.DATE)]),
    ("06-Oct-26", [("06-Oct-26", 20261006, U.DATE, K.DATE)]),
    ("03/31/2025", [("03/31/2025", 20250331, U.DATE, K.DATE)]),
    ("March 2025", [("March 2025", 20250300, U.DATE, K.DATE)]),
    ("Mar-25", [("Mar-25", 20250300, U.DATE, K.DATE)]),
    ("2025-03", [("2025-03", 20250300, U.DATE, K.DATE)]),
    # Years versus amounts
    ("2025", [("2025", 20250000, U.DATE, K.YEAR)]),
    ("founded in 1998", [("1998", 19980000, U.DATE, K.YEAR)]),
    ("2,025", [("2,025", 2025, U.COUNT)]),
    ("₹2025", [("₹2025", 2025, U.INR)]),
    # Period labels and durations
    ("FY26", [("FY26", 20260000, U.DATE, K.PERIOD)]),
    ("FY2026", [("FY2026", 20260000, U.DATE, K.PERIOD)]),
    ("FY25-26", [("FY25-26", 20260000, U.DATE, K.PERIOD)]),
    (f"FY23{EN_DASH}FY26", [(f"FY23{EN_DASH}FY26", 20260000, U.DATE, K.PERIOD)]),
    ("Q2 FY26", [("Q2 FY26", 20260000, U.DATE, K.PERIOD)]),
    ("H1FY26", [("H1FY26", 20260000, U.DATE, K.PERIOD)]),
    ("9MFY26", [("9MFY26", 20260000, U.DATE, K.PERIOD)]),
    ("FY26E", [("FY26E", 20260000, U.DATE, K.PERIOD)]),
    ("Q3 2025", [("Q3 2025", 20250000, U.DATE, K.PERIOD)]),
    ("H1", [("H1", 0, U.DATE, K.PERIOD)]),
    (f"2023{EN_DASH}2026", [(f"2023{EN_DASH}2026", 20260000, U.DATE, K.PERIOD)]),
    ("2011-12", [("2011-12", 20120000, U.DATE, K.PERIOD)]),
    ("3Y", [("3Y", 3, U.COUNT, K.PERIOD)]),
    ("1 year", [("1 year", 1, U.COUNT, K.PERIOD)]),
    ("a 5-year CAGR", [("5-year", 5, U.COUNT, K.PERIOD)]),
    # Locators, ordinals, list markers
    ("page 12", [("page 12", 12, U.PLAIN, K.LOCATOR)]),
    (f"pp. 12{EN_DASH}14", [(f"pp. 12{EN_DASH}14", 12, U.PLAIN, K.LOCATOR)]),
    ("slide 4", [("slide 4", 4, U.PLAIN, K.LOCATOR)]),
    ("section 3.2", [("section 3.2", "3.2", U.PLAIN, K.LOCATOR)]),
    ("Sheet1!B2:F20", [("Sheet1!B2:F20", 1, U.PLAIN, K.LOCATOR)]),
    ("as noted [3]", [("[3]", 3, U.PLAIN, K.LOCATOR)]),
    ("1. Revenue", [("1", 1, U.COUNT, K.LIST_MARKER)]),
    ("intro\n(2) Debt", [("2", 2, U.COUNT, K.LIST_MARKER)]),
    ("the 3rd largest", [("3rd", 3, U.COUNT, K.ORDINAL)]),
    # Not figures
    ("ISIN INE002A01018", []),
    ("EBITDA2", []),
    ("version 3.2.1", []),
    ("[p. 4](cite:0b9e7c1e-2f3a-4c55-9d0e-1b2c3d4e5f60#p.4)", [("p. 4", 4, U.PLAIN, K.LOCATOR)]),
    ("see https://example.com/2025/12/report", []),
    ("COVID-19", []),
    ("CIN L17110MH1973PLC019786", []),
    ("48 hours 5", [("48", 48, U.COUNT), ("5", 5, U.COUNT)]),
    # In sentences
    (
        "Revenue was ₹4,215 cr in FY25, and the margin 12.5%.",
        [
            ("₹4,215 cr", 42_150_000_000, U.INR),
            ("FY25", 20250000, U.DATE, K.PERIOD),
            ("12.5%", "12.5", U.PCT),
        ],
    ),
    # An impossible date falls back to its numbers.
    ("31/02/2025", [("31", 31, U.COUNT), ("02", 2, U.COUNT), ("2025", 20250000, U.DATE, K.YEAR)]),
]


def _id(case: tuple[str, list[Expected]]) -> str:
    return repr(case[0][:40])


@pytest.mark.parametrize("case", CASES, ids=_id)
def test_extract(case: tuple[str, list[Expected]]) -> None:
    text, expected = case
    figures = extract_figures(text)
    got = [(f.raw, f.value, f.unit, f.kind) for f in figures]
    want = [
        (e[0], Decimal(e[1]), e[2], e[3] if len(e) == 4 else K.NUMBER)  # type: ignore[misc]
        for e in expected
    ]
    assert got == want
    for f in figures:
        assert text[f.span[0] : f.span[1]] == f.raw


def test_there_are_at_least_60_extraction_cases() -> None:
    assert len(CASES) >= 60


def test_precision_follows_the_display() -> None:
    (f,) = extract_figures("₹4,215 cr")
    assert f.step == Decimal(10**7)
    assert f.number == 4215
    (f,) = extract_figures("₹4,215.37 cr")
    assert f.step == Decimal(10**5)
    (f,) = extract_figures("12.50%")
    assert f.step == Decimal("0.01")
    (f,) = extract_figures("March 2025")
    assert f.iso == "2025-03"
    (f,) = extract_figures("06-Oct-2026")
    assert f.iso == "2026-10-06"
    (f,) = extract_figures("2025")
    assert f.iso == "2025"
    assert extract_figures("5%")[0].iso is None


def test_signs() -> None:
    (minus,) = extract_figures("-5%")
    (paren,) = extract_figures("(1,234)")
    (plain,) = extract_figures("5%")
    assert (minus.signed, paren.signed, plain.signed) == (True, False, False)
    assert paren.value == -1234


def test_both_range_ends_share_the_range_span() -> None:
    text = f"margins of 10{EN_DASH}12% in FY26"
    low, high, _ = extract_figures(text)
    assert low.range_span == high.range_span
    assert low.range_span is not None
    assert text[low.range_span[0] : low.range_span[1]] == f"10{EN_DASH}12%"
    assert extract_figures("12%")[0].range_span is None


def test_values_are_identical_for_indian_and_international_grouping() -> None:
    (indian,) = extract_figures("1,00,000")
    (international,) = extract_figures("100,000")
    assert indian.value == international.value
