"""AC #2-#3: grounding sets and matching, table-driven.

Each case: the answer text (its first figure is checked), the sources, the expected
reason."""

from datetime import date
from decimal import Decimal

import pytest

from app.guardrails.grounding import (
    ExplicitValue,
    MatchReason,
    Source,
    SourceText,
    TableCell,
    build_grounding_set,
    extract_figures,
    ground_text,
    match,
)
from app.guardrails.grounding import Unit as U  # noqa: N817
from tests.guardrails.grounding.conftest import EN_DASH

M, NF, CC = MatchReason.MATCHED, MatchReason.NOT_FOUND, MatchReason.CROSS_CURRENCY


def S(text: str) -> SourceText:  # noqa: N802
    return SourceText(text, origin="src")


CASES: list[tuple[str, list[Source], MatchReason]] = [
    # Display precision
    ("₹4,215 cr", [S("₹4,215.37 cr")], M),
    ("₹4,200 cr", [S("₹4,215.37 cr")], NF),
    ("₹4,215.37 cr", [S("₹4,215 cr")], NF),  # more precise than its source
    ("₹4,215 cr", [S("₹4,214.5 cr")], M),  # lower bound included
    ("₹4,215 cr", [S("₹4,215.5 cr")], NF),  # upper bound excluded
    ("₹4,215 cr", [S("₹4,214.49 cr")], NF),
    ("₹4,215.4 cr", [S("₹4,215.37 cr")], M),
    ("12%", [S("12.4%")], M),
    ("12%", [S("12.6%")], NF),
    # Unit and scale equivalence
    ("₹42,150 million", [S("₹4,215 crore")], M),
    ("₹4,215 crore", [S("₹42,150 million")], M),
    ("₹42.15 bn", [S("₹4,215 crore")], M),
    ("42.15 bn", [S("₹4,215 crore")], M),  # no currency: matches the scaled amount
    ("1,200m", [S("1.2bn")], M),
    ("1.2bn", [S("1,234m")], M),
    ("1.2bn", [S("1,260m")], NF),
    ("₹1 lakh", [S("₹1,00,000")], M),
    ("1,00,000", [S("100,000")], M),
    ("₹10 lakh crore", [S("₹10 trillion")], M),
    ("$5m", [S("USD 5 million")], M),
    ("€3.2bn", [S("EUR 3,200 mn")], M),
    ("4,215", [S("₹4,215 cr")], M),  # the numeral as the source wrote it
    # Cross-currency never matches; a currency must be in the source
    ("$4,215 cr", [S("₹4,215 cr")], CC),
    ("₹505 mn", [S("US$ 505 mn")], CC),
    ("€10m", [S("$10m")], CC),
    ("₹4,215 cr", [S("4,215 cr")], NF),
    # Percent and basis points
    ("125 bps", [S("1.25%")], M),
    ("1.25%", [S("125 bps")], M),
    ("50 bps", [S("0.5%")], M),
    ("50 bps", [S("0.6%")], NF),
    ("12.5%", [TableCell("12.5", origin="t")], NF),  # no metadata: PLAIN, not PCT
    ("12.5%", [TableCell("12.5", origin="t", unit=U.PCT)], M),
    ("12.5%", [S("12.5x")], NF),
    # Multiples versus counts
    ("5x", [S("5 deals")], NF),
    ("5.2x", [S("5.2 times")], M),
    # Signs
    ("-5%", [S("-5%")], M),
    ("-5%", [S("5%")], NF),
    ("(1,234)", [S("1,234")], M),
    ("(1,234)", [S("-1,234")], M),
    ("-1,234", [S("1,234")], NF),
    ("a loss of ₹120 cr", [TableCell("(120)", origin="t", unit=U.INR, scale="crore")], M),
    # Dates at the figure's precision
    ("31 March 2025", [S("2025-03-31")], M),
    ("06-Oct-2026", [S("6 October 2026")], M),
    ("March 2025", [S("31/03/2025")], M),
    ("31 March 2025", [S("March 2025")], NF),
    ("2025", [S("FY2025")], M),
    ("2025", [S("2,025")], NF),  # a year is not the amount 2,025
    ("2,025", [S("in 2025")], NF),
    ("1998", [S("founded in 1998")], M),
    ("8 October 2026", [ExplicitValue(date(2026, 10, 8), U.DATE, "today")], M),
    # Table cells with metadata, explicit values
    ("₹4,215 cr", [TableCell("4,215", origin="t", unit=U.INR, scale="crore")], M),
    ("4,215", [TableCell("4,215", origin="t", unit=U.INR, scale="crore")], M),
    ("₹4,215", [TableCell("4,215", origin="t", unit=U.INR, scale="crore")], NF),  # mis-scaled
    ("₹4,215 cr", [TableCell(Decimal("4215.37"), origin="t", unit=U.INR, scale="cr")], M),
    ("18.4%", [ExplicitValue(Decimal("18.37"), U.PCT, "calc")], M),
    ("₹1,234.5 cr", [ExplicitValue("12345000000", U.INR, "calc")], M),
    ("₹100 cr", [TableCell(100, origin="t", unit=U.INR, scale=Decimal(10**7))], M),
    ("₹5 cr", [TableCell("₹5 cr", origin="t", unit=U.USD, scale="mn")], M),  # cell's own unit
]


def _id(case: tuple[str, list[Source], MatchReason]) -> str:
    return f"{case[0]!r}-{case[2].value}"


@pytest.mark.parametrize("case", CASES, ids=_id)
def test_match(case: tuple[str, list[Source], MatchReason]) -> None:
    text, sources, expected = case
    figure = extract_figures(text)[-1] if text.startswith("a loss") else extract_figures(text)[0]
    result = match(figure, build_grounding_set(sources))
    assert result.reason is expected
    assert result.matched is (expected is M)
    if expected is M:
        assert result.origins


def test_there_are_at_least_40_match_cases() -> None:
    assert len(CASES) >= 40


def test_each_end_of_a_range_must_be_grounded() -> None:
    answer = f"Margins of 10{EN_DASH}12% are expected."
    both = build_grounding_set([S("between 10% and 12%")])
    one = build_grounding_set([S("about 10%")])
    assert [r.reason for r in ground_text(answer, both)] == [M, M]
    assert [r.reason for r in ground_text(answer, one)] == [M, NF]


def test_origins_name_every_matching_source() -> None:
    sources = [
        SourceText("Revenue ₹4,215.37 cr", "doc1#p.3"),
        TableCell("4,215.4", "doc2!B4", unit=U.INR, scale="crore"),
        SourceText("Revenue ₹4,215.37 cr again", "doc1#p.3"),
        SourceText("EBITDA ₹812 cr", "doc3#p.1"),
    ]
    result = match(extract_figures("₹4,215 cr")[0], build_grounding_set(sources))
    assert result.origins == ("doc1#p.3", "doc2!B4")


def test_ground_text_reports_every_figure() -> None:
    sources = build_grounding_set(
        [S("Revenue was ₹4,215.37 crore in FY25, margin 18.2%, 1,250 employees")]
    )
    answer = (
        "1. Revenue: ₹42,154 million in FY25 (p. 4).\n"
        "2. Margin: 18% across 3 segments; staff 1,300."
    )
    results = [(r.figure.raw, r.reason) for r in ground_text(answer, sources)]
    assert results == [
        ("1", MatchReason.EXEMPT),
        ("₹42,154 million", M),
        ("FY25", MatchReason.EXEMPT),
        ("p. 4", MatchReason.EXEMPT),
        ("2", MatchReason.EXEMPT),
        ("18%", M),
        ("3", MatchReason.EXEMPT),
        ("1,300", NF),
    ]
    assert all(r.grounded for r in ground_text("FY26 had 3 deals", sources))
