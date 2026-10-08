"""AC #2 (set building) and AC #5 (purity, determinism, speed)."""

import ast
import statistics
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.guardrails import grounding
from app.guardrails.grounding import (
    ExplicitValue,
    GroundedValue,
    GroundingSet,
    SourceText,
    TableCell,
    build_grounding_set,
    extract_figures,
    ground_text,
)
from app.guardrails.grounding import Unit as U  # noqa: N817


def _values(gs: GroundingSet) -> set[tuple[Decimal, U, str]]:
    return {(e.value, e.unit, e.origin) for e in gs}


def test_a_set_from_mixed_texts_and_cells() -> None:
    gs = build_grounding_set(
        [
            SourceText("Revenue ₹4,215 cr; margin 18.2%; page 4", "doc#p.4"),
            TableCell("12.5", "sheet!C3", unit=U.PCT),
            TableCell("1.2", "sheet!C4"),
            TableCell("(45)", "sheet!C5", unit=U.INR, scale="crore"),
            ExplicitValue(Decimal("4627.3"), U.PLAIN, "calc:sum"),
            ExplicitValue(date(2026, 10, 8), U.DATE, "today"),
        ]
    )
    values = _values(gs)
    assert (Decimal(42_150_000_000), U.INR, "doc#p.4") in values
    assert (Decimal(4215), U.PLAIN, "doc#p.4") in values  # the numeral as written
    assert (Decimal("18.2"), U.PCT, "doc#p.4") in values
    assert not any(v == 4 and o == "doc#p.4" for v, _, o in values)  # locators ground nothing
    assert (Decimal("12.5"), U.PCT, "sheet!C3") in values
    assert (Decimal("1.2"), U.PLAIN, "sheet!C4") in values  # no metadata: PLAIN only
    assert not any(o == "sheet!C4" and u is U.PCT for _, u, o in values)
    assert (Decimal(-450_000_000), U.INR, "sheet!C5") in values
    assert (Decimal("4627.3"), U.PLAIN, "calc:sum") in values
    today = {v for v, u, o in values if o == "today"}
    assert today == {Decimal(20261008), Decimal(20261000), Decimal(20260000)}


def test_periods_ground_their_year_and_nothing_else() -> None:
    values = _values(build_grounding_set([SourceText("FY26 and H1", "d")]))
    assert values == {(Decimal(20260000), U.DATE, "d")}


def test_sets_combine() -> None:
    a = build_grounding_set([SourceText("₹5 cr", "a")])
    b = build_grounding_set([SourceText("12%", "b")])
    assert len(a | b) == len(a) + len(b)
    assert isinstance(next(iter(a)), GroundedValue)


def test_explicit_values_must_be_numbers() -> None:
    with pytest.raises(ValueError, match="not a number"):
        build_grounding_set([ExplicitValue("n/a", U.PLAIN, "calc")])


def test_deterministic() -> None:
    text = _PARAGRAPH * 3
    assert extract_figures(text) == extract_figures(text)
    gs = build_grounding_set([SourceText(text, "d")])
    assert ground_text(text, gs) == ground_text(text, gs)
    assert all(r.grounded for r in ground_text(text, gs))  # a text grounds itself


def test_the_library_is_pure() -> None:
    allowed = {
        "bisect",
        "collections",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "functools",
        "re",
        "types",
        "typing",
        "app",
    }
    package = Path(grounding.__file__).parent
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            for name in names:
                root = name.split(".")[0]
                assert root in allowed, f"{path.name} imports {name}"
                if root == "app":
                    assert name.startswith("app.guardrails.grounding"), name
        assert "open(" not in path.read_text(), path.name


# --- AC #5: speed ---------------------------------------------------------------------

_PARAGRAPH = (
    "Acme Industries reported revenue of ₹4,215.37 crore in FY25, up 12.4% YoY, with "
    "EBITDA of ₹812 cr (margin 19.3%) and PAT of ₹312.4 cr. Net debt fell to ₹1,234 cr, "
    "or 1.5x EBITDA, after a 25 bps cut in the coupon; cash was ₹1,23,45,678. The board "
    "met on 14 May 2025 and approved capex of $120 mn for 2025-26; the order book stood "
    "at 18,450 units across 214 customers in 9 states (p. 12). Q2 FY26 guidance is "
    "₹1,050-1,100 cr. Employees: 1,00,250. EPS (2.3) versus 4.1 last year.\n"
)


def _thousand_tokens() -> str:
    # ~1,000 tokens: about 750 words.
    words = 0
    parts = []
    while words < 750:
        parts.append(_PARAGRAPH)
        words += len(_PARAGRAPH.split())
    return "".join(parts)


@pytest.mark.perf
def test_a_1000_token_text_is_processed_within_5ms() -> None:
    text = _thousand_tokens()
    gs = build_grounding_set([SourceText(text, "d")])
    for _ in range(20):
        ground_text(text, gs)
    # Best of three rounds: a background process can spoil one round, not the code's p95.
    rounds = []
    for _ in range(3):
        samples = []
        for _ in range(200):
            start = time.perf_counter()
            ground_text(text, gs)
            samples.append((time.perf_counter() - start) * 1000)
        rounds.append(statistics.quantiles(samples, n=20)[-1])
    p95 = min(rounds)
    assert len(extract_figures(text)) > 100
    assert p95 <= 5.0, f"p95 {p95:.2f} ms"


@pytest.mark.parametrize(
    "text",
    ["1" * 20_000, "1," * 10_000, "(" * 5_000 + "5" + ")" * 5_000, "'" * 20_000, "March " * 3_000],
    ids=["digits", "commas", "parens", "quotes", "months"],
)
def test_no_catastrophic_backtracking(text: str) -> None:
    start = time.perf_counter()
    extract_figures(text)
    assert time.perf_counter() - start < 0.5
