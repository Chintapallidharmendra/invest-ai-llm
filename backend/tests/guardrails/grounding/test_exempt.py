"""AC #4: exemptions. Each case: text, the figure's raw text, whether it is exempt."""

import pytest

from app.guardrails.grounding import extract_figures, is_exempt
from tests.guardrails.grounding.conftest import EN_DASH

CASES: list[tuple[str, str, bool]] = [
    # Small counts without a unit
    ("We reviewed 3 deals", "3", True),
    ("over 12 months", "12", True),
    ("0 defaults", "0", True),
    ("13 deals closed", "13", False),
    ("-3 deals", "-3", False),
    ("10 to 12 bidders", "10", True),
    ("2.5 deals", "2.5", False),
    # Units always need grounding, however small
    ("₹5 cr", "₹5 cr", False),
    ("5%", "5%", False),
    ("5x", "5x", False),
    ("5 lakh", "5 lakh", False),
    # List markers and ordinals
    ("1. Revenue grew", "1", True),
    ("intro\n(2) Debt rose", "2", True),
    ("the 3rd largest", "3rd", True),
    ("the 23rd largest", "23rd", False),
    # Period labels
    ("FY26", "FY26", True),
    ("Q2 FY26", "Q2 FY26", True),
    ("H1", "H1", True),
    ("FY 2025", "FY 2025", True),
    ("1 year", "1 year", True),
    ("3Y CAGR", "3Y", True),
    (f"2023{EN_DASH}2026", f"2023{EN_DASH}2026", True),
    # Years inside a period phrase
    ("fiscal 2025", "2025", True),
    ("calendar year 2024", "2024", True),
    ("the 2025 financial year", "2025", True),
    ("founded in 2025", "2025", False),
    # Locators inside citations
    ("page 12", "page 12", True),
    ("(CIM, p. 14)", "p. 14", True),
    ("slide 4", "slide 4", True),
    ("section 3.2", "section 3.2", True),
    ("Sheet1!B2:F20", "Sheet1!B2:F20", True),
    ("as stated [3]", "[3]", True),
    ("[CIM 2025](cite:0b9e7c1e#p.3)", "2025", True),
    ("CIM 2025 (cite:0b9e7c1e#p.3)", "2025", False),
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c[0][:30]!r}-{c[2]}")
def test_exempt(case: tuple[str, str, bool]) -> None:
    text, raw, expected = case
    (figure,) = [f for f in extract_figures(text) if f.raw == raw]
    assert is_exempt(figure, text) is expected


def test_there_are_at_least_20_exemption_cases() -> None:
    assert len(CASES) >= 20


def test_context_free_exemptions_need_no_context() -> None:
    (figure,) = extract_figures("3 deals")
    assert is_exempt(figure)
    (year,) = extract_figures("fiscal 2025")
    assert not is_exempt(year)  # the period phrase is only visible with context
