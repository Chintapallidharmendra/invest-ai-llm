"""Compiled Polars expressions agree with the pure-Python reference evaluator (AC #4)."""

import math
from typing import Any, Final

import pytest

from app.sheets.expressions import ColumnType, compile_expression, parse_expression
from tests.sheets.expressions.conftest import ROWS, SCHEMA, frame
from tests.sheets.expressions.reference import evaluate_rows

# Each expression is run over every row in ROWS (nulls, zeros, negatives, halves,
# huge crore amounts and tiny values), so each case covers the edge rows too.
CASES: Final = [
    # Arithmetic and precedence
    "[Revenue] - [Cost]",
    "[Revenue] + [Cost] * 2",
    "([Revenue] + [Cost]) * 2",
    "[Revenue] - [Cost] - [Units]",
    "[Units] * [Units] + 1",
    "-[Revenue]",
    "- -[Units]",
    "-(Revenue - Cost)",
    "Units - 10",
    "[Units] + [*]",
    "1 + 2 * 3 - 4",
    "2.5e3 + .5",
    # Division: always float, zero denominator is null
    "[Revenue] / [Cost]",
    "[Units] / 2",
    "[Units] / [Zero]",
    "[Revenue] / [Units]",
    "0 / 0",
    "1 / ([Units] - [Units])",
    "([Revenue] - [Cost]) / [Revenue] * 100",
    "[a]]b] / [Margin %]",
    # Comparisons
    "[Revenue] > [Cost]",
    "[Revenue] >= 100",
    "[Units] < 1",
    "[Units] <= 0",
    "[Units] = 0",
    "[Units] <> 0",
    "[Units] = 7.0",
    '[Region] = "North"',
    '[Region] <> "north"',
    '[Region] < "P"',
    "[Active] = TRUE",
    "[Day] < [Day2]",
    "[Day] = [Day2]",
    # Logic (three-valued)
    "[Active] AND [Revenue] > 0",
    "[Active] OR [Units] > 5",
    "NOT [Active]",
    "not active or false",
    "[Active] and not ([Units] < 0)",
    "TRUE AND [Active]",
    "FALSE AND [Active]",
    "TRUE OR [Active]",
    # IF
    "IF([Active], [Revenue], [Cost])",
    "IF([Units] > 0, [Units], 0)",
    "IF([Units] > 0, [Units], 0.5)",
    'IF([Region] = "North", "N", "other")',
    "IF([Revenue] > [Cost], [Revenue] / [Cost], -1)",
    "IF([Active], [Day], [Day2]) = [Day2]",
    "if(active, 1, if(units > 0, 2, 3))",
    # ROUND (half away from zero), ABS, MIN, MAX
    "ROUND([Margin %])",
    "ROUND([Margin %], 2)",
    "ROUND([Ünïcode name], 2)",
    "ROUND([Ünïcode name])",
    "ROUND([Revenue] / 3, 4)",
    "ROUND([Units] * 1234, -2)",
    "ROUND([Revenue], -3)",
    "ROUND([Units], 2)",
    "round(-[Margin %], 1)",
    "ABS([Revenue])",
    "ABS([Units])",
    "ABS(-[Margin %]) + 1",
    "MIN([Revenue], [Cost])",
    "MAX([Revenue], [Cost], [Units])",
    "MIN([Units], [*])",
    "MAX([Units])",
    "MIN([Revenue], 0)",
    # Mixed
    "ROUND(([Revenue] - [Cost]) / [Revenue] * 100, 1) >= 50",
    "IF([Active] AND [Units] > 0, ROUND([Revenue] / [Units], 2), 0)",
    "[Ünïcode name] * 2",
]


def _same(actual: Any, expected: Any) -> bool:
    if actual is None or expected is None:
        return actual is expected
    if isinstance(expected, float) or isinstance(actual, float):
        return math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)
    return bool(actual == expected)


def test_enough_cases() -> None:
    assert len(CASES) >= 50
    assert len(set(CASES)) == len(CASES)


@pytest.mark.parametrize("text", CASES)
def test_matches_reference(text: str) -> None:
    compiled = compile_expression(text, SCHEMA)
    actual = frame().with_columns(compiled.expr.alias("out"))["out"].to_list()
    expected = evaluate_rows(parse_expression(text, SCHEMA), ROWS)
    assert len(actual) == len(expected)
    mismatches = [
        (i, a, e) for i, (a, e) in enumerate(zip(actual, expected, strict=True)) if not _same(a, e)
    ]
    assert not mismatches, mismatches


def _column(text: str) -> list[Any]:
    return frame().with_columns(compile_expression(text, SCHEMA).expr.alias("out"))["out"].to_list()


def test_round_half_away_from_zero() -> None:
    assert _column("ROUND(2.5)")[0] == 3
    assert _column("ROUND(-2.5)")[0] == -3
    assert _column("ROUND(0.5)")[0] == 1
    assert _column("ROUND(2.675, 2)")[0] == pytest.approx(2.68)
    assert _column("ROUND(-1250, -2)")[0] == -1300


def test_division_by_zero_is_null_never_inf() -> None:
    assert _column("[Units] / [Zero]") == [None] * len(ROWS)
    assert _column("1.5 / 0") == [None] * len(ROWS)
    values = _column("[Revenue] / [Cost]")
    assert all(v is None or math.isfinite(v) for v in values)


def test_integer_division_yields_float() -> None:
    compiled = compile_expression("[Units] / 2", SCHEMA)
    assert compiled.dtype is ColumnType.NUMBER
    assert _column("[Units] / 2")[0] == 3.5


def test_null_propagates_through_arithmetic() -> None:
    assert _column("[Revenue] + 1")[2] is None
    assert _column("ROUND([Revenue], 1)")[2] is None
    assert _column("IF([Active], 1, 2)")[2] is None


def test_min_max_skip_nulls_like_excel_blanks() -> None:
    assert _column("MIN([Revenue], [Cost])")[2] == 12.5
    assert _column("MAX([Revenue], [Units])")[2] is None
