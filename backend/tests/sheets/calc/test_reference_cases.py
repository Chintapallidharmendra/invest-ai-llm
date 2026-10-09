"""Reference cases (AC #1, #5): each expected value was computed independently with
exact fractions (and float powers for CAGR) and rounded half-up to 2 decimals."""

from decimal import Decimal
from typing import Final

import pytest

from app.guardrails.grounding import Unit
from app.sheets.calc import CalcInput, Metric, calculate

Value = tuple[str, str] | tuple[str, str, str]  # (value, unit[, scale word])

# fmt: off
CASES: Final[list[tuple[str, list[Value], str | int | None, str]]] = [
    # growth: (b - a) / |a|, %
    ("growth", [("100", "INR"), ("125", "INR")], None, "25.00"),
    ("growth", [("1000", "INR"), ("1250", "INR")], None, "25.00"),
    ("growth", [("812.4", "INR", "crore"), ("1250.0", "INR", "crore")], None, "53.87"),
    ("growth", [("4215.37", "INR", "crore"), ("3987.10", "INR", "crore")], None, "-5.42"),
    ("growth", [("-200", "PLAIN"), ("100", "PLAIN")], None, "150.00"),
    ("growth", [("-200", "PLAIN"), ("-300", "PLAIN")], None, "-50.00"),
    ("growth", [("3", "PCT"), ("3.0001", "PCT")], None, "0.00"),
    ("growth", [("1", "INR", "lakh"), ("1", "INR", "crore")], None, "9900.00"),
    ("growth", [("99999999", "INR", "crore"), ("100000000", "INR", "crore")], None, "0.00"),
    ("growth", [("7", "COUNT"), ("3", "COUNT")], None, "-57.14"),
    ("growth", [("0.0001", "PCT"), ("0.0003", "PCT")], None, "200.00"),
    ("growth", [("12345.678", "USD", "million"), ("12345.679", "USD", "million")], None, "0.00"),
    ("growth", [("500", "EUR"), ("0", "EUR")], None, "-100.00"),
    ("growth", [("2", "PLAIN"), ("2.0049", "PLAIN")], None, "0.25"),
    ("growth", [("2", "PLAIN"), ("2.0050", "PLAIN")], None, "0.25"),
    # margin: part / whole, %
    ("margin", [("250", "INR"), ("1000", "INR")], None, "25.00"),
    ("margin", [("1", "INR"), ("3", "INR")], None, "33.33"),
    ("margin", [("2", "INR"), ("3", "INR")], None, "66.67"),
    ("margin", [("250", "INR", "crore"), ("10000", "INR", "lakh")], None, "250.00"),
    ("margin", [("-45.5", "INR", "crore"), ("910", "INR", "crore")], None, "-5.00"),
    ("margin", [("0.01", "INR"), ("100000", "INR")], None, "0.00"),
    ("margin", [("1234.5678", "USD", "million"), ("9876.54321", "USD", "million")], None, "12.50"),
    ("margin", [("1", "INR"), ("8", "INR")], None, "12.50"),
    ("margin", [("17", "PLAIN"), ("400", "COUNT")], None, "4.25"),
    ("margin", [("3", "INR", "crore"), ("0.5", "INR", "billion")], None, "6.00"),
    # ratio
    ("ratio", [("85", "PLAIN"), ("100", "PLAIN")], None, "0.85"),
    ("ratio", [("2", "INR"), ("3", "INR")], None, "0.67"),
    ("ratio", [("1500", "INR", "crore"), ("2000", "INR", "crore")], None, "0.75"),
    ("ratio", [("1", "COUNT"), ("7", "COUNT")], None, "0.14"),
    ("ratio", [("-5", "PLAIN"), ("8", "PLAIN")], None, "-0.63"),
    ("ratio", [("123456789", "INR"), ("0.001", "INR")], None, "123456789000.00"),
    ("ratio", [("0.4475", "PLAIN"), ("1", "PLAIN")], None, "0.45"),
    ("ratio", [("0.4485", "PLAIN"), ("1", "PLAIN")], None, "0.45"),
    # multiple, x
    ("multiple", [("12400", "INR", "crore"), ("1000", "INR", "crore")], None, "12.40"),
    ("multiple", [("8.5", "INR", "billion"), ("650", "INR", "crore")], None, "1.31"),
    ("multiple", [("100", "USD"), ("3", "USD")], None, "33.33"),
    ("multiple", [("45000", "INR", "crore"), ("3712.5", "INR", "crore")], None, "12.12"),
    ("multiple", [("1", "INR"), ("16", "INR")], None, "0.06"),
    ("multiple", [("2.345", "PLAIN"), ("1", "PLAIN")], None, "2.35"),
    # sum (shown in the inputs' shared scale, else base units)
    ("sum", [("4215", "INR", "crore"), ("1000", "INR", "crore")], None, "5215.00"),
    ("sum", [("-1", "PLAIN"), ("-2.5", "PLAIN"), ("-3.25", "PLAIN")], None, "-6.75"),
    ("sum", [("0.1", "PCT"), ("0.2", "PCT")], None, "0.30"),
    ("sum", [("1", "INR", "crore"), ("50", "INR", "lakh")], None, "15000000.00"),
    ("sum", [("99999999999.995", "INR")], None, "100000000000.00"),
    ("sum", [("1.005", "INR"), ("1.005", "INR")], None, "2.01"),
    ("sum", [("3", "COUNT"), ("4", "PLAIN")], None, "7.00"),
    ("sum", [("123.456", "USD", "million"), ("0.004", "USD", "million")], None, "123.46"),
    # mean
    ("mean", [("4215.5", "INR", "crore")], None, "4215.50"),
    ("mean", [("1", "PLAIN"), ("2", "PLAIN")], None, "1.50"),
    ("mean", [("1", "PLAIN"), ("2", "PLAIN"), ("2", "PLAIN")], None, "1.67"),
    ("mean", [("-4215.5", "USD", "million"), ("1000", "USD", "million")], None, "-1607.75"),
    ("mean", [("10", "PCT"), ("12.5", "PCT"), ("15.25", "PCT"), ("9.75", "PCT")], None, "11.88"),
    ("mean", [("1", "INR", "crore"), ("50", "INR", "lakh")], None, "7500000.00"),
    ("mean", [("0.005", "PLAIN"), ("0.005", "PLAIN"), ("0.005", "PLAIN")], None, "0.01"),
    # cagr: (end / start)^(1/years) - 1, %
    ("cagr", [("812.4", "INR", "crore"), ("1250.0", "INR", "crore")], 3, "15.45"),
    ("cagr", [("100", "INR"), ("200", "INR")], 1, "100.00"),
    ("cagr", [("100", "INR"), ("200", "INR")], 2, "41.42"),
    ("cagr", [("100", "INR"), ("150", "INR")], "2.5", "17.61"),
    ("cagr", [("1000", "INR"), ("500", "INR")], 5, "-12.94"),
    ("cagr", [("1", "INR", "lakh"), ("1", "INR", "crore")], 10, "58.49"),
    ("cagr", [("100", "INR"), ("100", "INR")], 4, "0.00"),
    ("cagr", [("100", "INR"), ("0", "INR")], 3, "-100.00"),
    ("cagr", [("3500", "INR", "crore"), ("6200", "INR", "crore")], 7, "8.51"),
    ("cagr", [("2.5", "PCT"), ("3.1", "PCT")], 2, "11.36"),
    ("cagr", [("45", "USD", "million"), ("1.2", "USD", "billion")], 12, "31.47"),
    ("cagr", [("100", "INR"), ("121", "INR")], 2, "10.00"),
    ("cagr", [("64", "INR"), ("125", "INR")], 3, "25.00"),
]
# fmt: on


def _input(spec: Value) -> CalcInput:
    value, unit, *scale = spec
    return CalcInput(Decimal(value), Unit(unit), scale=scale[0] if scale else None)


def test_enough_cases_for_every_metric() -> None:
    assert len(CASES) >= 60
    assert {metric for metric, *_ in CASES} == {m.value for m in Metric}


@pytest.mark.parametrize(("metric", "inputs", "years", "expected"), CASES)
def test_reference_case(
    metric: str, inputs: list[Value], years: str | int | None, expected: str
) -> None:
    result = calculate(metric, [_input(spec) for spec in inputs], years=years)
    assert result.display_value == Decimal(expected)
    assert str(result.display_value) == expected  # always exactly 2 decimals
