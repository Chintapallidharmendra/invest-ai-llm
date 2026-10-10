"""Inputs, results, errors and grounding of the calculation engine (AC #1-#5)."""

import ast
import random
import uuid
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Final

import pytest

import app.sheets.calc as calc_module
from app.guardrails import grounding as g
from app.guardrails.grounding import Unit
from app.sheets.calc import (
    CONTEXT,
    CalcError,
    CalcInput,
    ErrorCode,
    InputRef,
    Metric,
    calculate,
)

MINUS: Final = chr(0x2212)
DOC: Final = uuid.UUID("00000000-0000-7000-8000-00000000d0c1")


def inr(value: str, scale: str | None = None, period: str | None = None) -> CalcInput:
    return CalcInput(Decimal(value), Unit.INR, scale=scale, period=period)


# --- AC #1: metrics, Decimal only ---------------------------------------------------------


def test_cagr_example_from_the_story() -> None:
    result = calculate(
        Metric.CAGR,
        [inr("812.4", "crore", "FY22"), inr("1250.0", "crore", "FY25")],
    )
    assert result.display == "15.45%"
    assert result.formula == f"CAGR = (1,250.0 / 812.4)^(1/3) {MINUS} 1 = 15.45%"
    assert result.years == 3
    assert result.unit is Unit.PCT


def test_values_are_decimals_never_floats() -> None:
    result = calculate("growth", [inr("100"), inr("133")])
    assert isinstance(result.value, Decimal)
    assert isinstance(result.display_value, Decimal)
    with pytest.raises(TypeError):
        CalcInput(1.5, Unit.INR)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        calculate("cagr", [inr("1"), inr("2")], years=2.5)  # type: ignore[arg-type]
    assert CalcInput(7, Unit.COUNT).value == Decimal(7)  # type: ignore[arg-type]
    assert CalcInput("1.25", Unit.PCT).value == Decimal("1.25")  # type: ignore[arg-type]


def test_context_precision_is_34_digits() -> None:
    assert CONTEXT.prec == 34
    result = calculate(
        "ratio", [CalcInput(Decimal(1), Unit.PLAIN), CalcInput(Decimal(3), Unit.PLAIN)]
    )
    assert len(result.value.as_tuple().digits) == 34


def test_ambient_context_does_not_matter() -> None:
    with localcontext() as ctx:
        ctx.prec = 3
        result = calculate("ratio", [inr("2"), inr("3")])
    assert len(result.value.as_tuple().digits) == 34


def test_cagr_fractional_years() -> None:
    result = calculate("cagr", [inr("100"), inr("150")], years="2.5")
    assert result.display == "17.61%"
    assert "^(1/2.5)" in result.formula


# --- AC #2: inputs and refs -----------------------------------------------------------


def test_every_input_is_echoed_with_its_ref() -> None:
    refs = [InputRef(DOC, "p.12"), InputRef(DOC, "Sheet1!C4")]
    inputs = [
        CalcInput(Decimal("250"), Unit.INR, scale="crore", period="FY25", ref=refs[0]),
        CalcInput(Decimal("1000"), Unit.INR, scale="crore", period="FY25", ref=refs[1]),
    ]
    result = calculate("margin", inputs)
    assert result.inputs == tuple(inputs)
    assert [i.ref for i in result.inputs] == refs


def test_lakh_and_crore_are_normalised_by_scale() -> None:
    result = calculate("margin", [inr("250", "crore"), inr("10000", "lakh")])
    assert result.display == "250.00%"
    # Scales differ, so the formula shows them.
    assert result.formula == "Margin = 250 crore / 10,000 lakh = 250.00%"


def test_numeric_scale_factor() -> None:
    result = calculate(
        "ratio",
        [CalcInput(Decimal(5), Unit.INR, scale=Decimal(1000)), inr("10000")],
    )
    assert result.display_value == Decimal("0.50")


def test_unknown_scale_word_is_refused() -> None:
    with pytest.raises(KeyError):
        inr("1", "zillion")


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (CalcInput(Decimal(1), Unit.INR), CalcInput(Decimal(1), Unit.USD)),
        (CalcInput(Decimal(1), Unit.EUR), CalcInput(Decimal(1), Unit.INR)),
        (CalcInput(Decimal(1), Unit.PCT), CalcInput(Decimal(1), Unit.INR)),
        (CalcInput(Decimal(1), Unit.MULTIPLE), CalcInput(Decimal(1), Unit.PLAIN)),
        (CalcInput(Decimal(1), Unit.PCT), CalcInput(Decimal(1), Unit.BPS)),
        (CalcInput(Decimal(20260101), Unit.DATE), CalcInput(Decimal(20260101), Unit.DATE)),
    ],
)
def test_mismatched_units(a: CalcInput, b: CalcInput) -> None:
    for metric in ("growth", "margin", "ratio", "sum"):
        with pytest.raises(CalcError) as caught:
            calculate(metric, [a, b])
        assert caught.value.code is ErrorCode.MISMATCHED_UNITS


def test_mixed_currencies_message() -> None:
    with pytest.raises(CalcError) as caught:
        calculate("sum", [inr("1"), CalcInput(Decimal(1), Unit.USD)])
    assert caught.value.message == "The values are in different currencies."


def test_plain_and_count_combine() -> None:
    result = calculate(
        "sum", [CalcInput(Decimal(3), Unit.COUNT), CalcInput(Decimal(4), Unit.PLAIN)]
    )
    assert result.unit is Unit.PLAIN


# --- AC #3: result shape --------------------------------------------------------------


@pytest.mark.parametrize(
    ("metric", "inputs", "unit", "display", "formula"),
    [
        ("growth", [inr("1000"), inr("1250")], Unit.PCT, "25.00%",
         f"Growth = (1,250 {MINUS} 1,000) / |1,000| = 25.00%"),
        ("growth", [CalcInput(Decimal(-200), Unit.PLAIN), CalcInput(Decimal(100), Unit.PLAIN)],
         Unit.PCT, "150.00%", f"Growth = (100 {MINUS} ({MINUS}200)) / |{MINUS}200| = 150.00%"),
        ("margin", [inr("1"), inr("8")], Unit.PCT, "12.50%", "Margin = 1 / 8 = 12.50%"),
        ("ratio", [inr("2"), inr("3")], Unit.PLAIN, "0.67", "Ratio = 2 / 3 = 0.67"),
        ("multiple", [inr("12400", "crore"), inr("1000", "crore")], Unit.MULTIPLE, "12.40x",
         "Multiple = 12,400 / 1,000 = 12.40x"),
        ("sum", [inr("4215", "crore"), inr("1000", "crore")], Unit.INR, "₹5,215.00 crore",
         "Sum = 4,215 + 1,000 = ₹5,215.00 crore"),
        ("sum", [inr("-1"), inr("-2.5")], Unit.INR, f"{MINUS}₹3.50",
         f"Sum = ({MINUS}1) + ({MINUS}2.5) = {MINUS}₹3.50"),
        ("mean", [CalcInput(Decimal("-4215.5"), Unit.USD, scale="million"),
                  CalcInput(Decimal(1000), Unit.USD, scale="million")], Unit.USD,
         f"{MINUS}$1,607.75 million",
         f"Mean = (({MINUS}4,215.5) + 1,000) / 2 = {MINUS}$1,607.75 million"),
        ("mean", [CalcInput(Decimal("12.5"), Unit.PCT)], Unit.PCT, "12.50%",
         "Mean = (12.5) / 1 = 12.50%"),
        ("cagr", [inr("100"), inr("0")], Unit.PCT, f"{MINUS}100.00%",
         f"CAGR = (0 / 100)^(1/3) {MINUS} 1 = {MINUS}100.00%"),
    ],
)  # fmt: skip
def test_result_unit_display_and_formula(
    metric: str, inputs: list[CalcInput], unit: Unit, display: str, formula: str
) -> None:
    result = calculate(metric, inputs, years=3 if metric == "cagr" else None)
    assert result.unit is unit
    assert result.display == display
    assert result.formula == formula


def test_sum_of_mixed_scales_is_shown_in_base_units() -> None:
    result = calculate("sum", [inr("1", "crore"), inr("50", "lakh")])
    assert result.display == "₹15,000,000.00"
    assert result.scale is None
    assert result.value == Decimal(15_000_000)


@pytest.mark.parametrize(
    ("value", "shown"),
    [("2.345", "2.35"), ("2.344999", "2.34"), ("-2.345", "-2.35"), ("0.005", "0.01")],
)
def test_display_rounds_half_up(value: str, shown: str) -> None:
    result = calculate(
        "multiple", [CalcInput(Decimal(value), Unit.PLAIN), CalcInput(Decimal(1), Unit.PLAIN)]
    )
    assert result.display_value == Decimal(shown)
    assert result.value == Decimal(value)  # full precision is kept


def test_grounding_value_round_trips_through_match() -> None:
    result = calculate("cagr", [inr("812.4", "crore", "FY22"), inr("1250.0", "crore", "FY25")])
    explicit = result.as_grounding_value()
    assert explicit == g.ExplicitValue(result.value, Unit.PCT, "calc:cagr")
    grounding_set = g.build_grounding_set([explicit])
    for text, ok in [
        ("CAGR was 15.45% over three years", True),
        ("CAGR was 15.4% over three years", True),  # shown at 1 dp still rounds to it
        ("CAGR was 15.46% over three years", False),
        ("CAGR was 16% over three years", False),
    ]:
        matched = [r for r in g.ground_text(text, grounding_set) if r.figure.unit is Unit.PCT]
        assert [r.matched for r in matched] == [ok], text


def test_grounding_of_a_money_result_in_crore() -> None:
    result = calculate("sum", [inr("4215", "crore"), inr("1000", "crore")])
    grounding_set = g.build_grounding_set([result.as_grounding_value("calc:1")])
    results = g.ground_text("Combined revenue was ₹5,215 crore.", grounding_set)
    assert [r.matched for r in results] == [True]
    assert results[0].origins == ("calc:1",)


def test_display_value_grounds_at_its_2dp_step() -> None:
    result = calculate("ratio", [inr("2"), inr("3")])
    grounding_set = g.build_grounding_set([result.as_grounding_value()])
    figure = g.extract_figures(f"ratio {result.display}")[0]
    assert figure.step == Decimal("0.01")
    assert g.match(figure, grounding_set).matched


# --- AC #4: errors --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metric", "inputs", "years", "code"),
    [
        ("growth", [inr("0"), inr("5")], None, ErrorCode.ZERO_DENOMINATOR),
        ("margin", [inr("5"), inr("0")], None, ErrorCode.ZERO_DENOMINATOR),
        ("ratio", [inr("5"), inr("0.00", "crore")], None, ErrorCode.ZERO_DENOMINATOR),
        ("multiple", [inr("5"), inr("0")], None, ErrorCode.ZERO_DENOMINATOR),
        ("cagr", [inr("0"), inr("5")], 2, ErrorCode.NEGATIVE_BASE),
        ("cagr", [inr("-10"), inr("5")], 2, ErrorCode.NEGATIVE_BASE),
        ("cagr", [inr("10"), inr("-5")], 2, ErrorCode.NEGATIVE_BASE),
        ("cagr", [inr("-10"), inr("-5")], 2, ErrorCode.NEGATIVE_BASE),
        ("cagr", [inr("10"), inr("20")], 0, ErrorCode.INVALID_YEARS),
        ("cagr", [inr("10"), inr("20")], -1, ErrorCode.INVALID_YEARS),
        ("cagr", [inr("10"), inr("20")], "abc", ErrorCode.INVALID_YEARS),
        ("cagr", [inr("10"), inr("20")], "NaN", ErrorCode.INVALID_YEARS),
        ("cagr", [inr("10"), inr("20")], None, ErrorCode.INVALID_YEARS),
        ("cagr", [inr("10", period="FY22"), inr("20", period="FY25")], 2,
         ErrorCode.MISMATCHED_PERIODS),
        ("cagr", [inr("10", period="FY25"), inr("20", period="FY22")], None,
         ErrorCode.MISMATCHED_PERIODS),
        ("growth", [inr("10", period="FY25"), inr("20", period="fy25")], None,
         ErrorCode.MISMATCHED_PERIODS),
        ("margin", [inr("10", period="FY25"), inr("20", period="FY24")], None,
         ErrorCode.MISMATCHED_PERIODS),
        ("ratio", [inr("10", period="Q1 FY26"), inr("20", period="Q2 FY26")], None,
         ErrorCode.MISMATCHED_PERIODS),
        ("growth", [inr("10")], None, ErrorCode.TOO_FEW_INPUTS),
        ("margin", [], None, ErrorCode.TOO_FEW_INPUTS),
        ("sum", [], None, ErrorCode.TOO_FEW_INPUTS),
        ("mean", [], None, ErrorCode.TOO_FEW_INPUTS),
        ("ratio", [inr("1"), inr("2"), inr("3")], None, ErrorCode.TOO_MANY_INPUTS),
        ("growth", [inr("1"), CalcInput(Decimal(1), Unit.USD)], None, ErrorCode.MISMATCHED_UNITS),
    ],
)  # fmt: skip
def test_error_codes(
    metric: str, inputs: list[CalcInput], years: int | str | None, code: ErrorCode
) -> None:
    with pytest.raises(CalcError) as caught:
        calculate(metric, inputs, years=years)
    assert caught.value.code is code
    assert str(caught.value) == caught.value.message
    # Messages are user-facing and carry no values, periods or labels.
    for item in inputs:
        assert str(item.value) not in caught.value.message or str(item.value) in "0123"
        if item.period:
            assert item.period.casefold() not in caught.value.message.casefold()


def test_every_error_code_is_covered() -> None:
    covered = {"zero_denominator", "negative_base", "invalid_years", "mismatched_periods",
               "too_few_inputs", "too_many_inputs", "mismatched_units"}  # fmt: skip
    assert covered == {c.value for c in ErrorCode}


def test_years_inferred_from_period_labels() -> None:
    for start, end, years in [("FY22", "FY25", 3), ("2019", "2024", 5), ("FY2020", "FY'26", 6),
                              ("CY21", "CY23", 2)]:  # fmt: skip
        result = calculate("cagr", [inr("100", period=start), inr("200", period=end)])
        assert result.years == years
    # Labels that aren't years need an explicit count.
    result = calculate(
        "cagr", [inr("100", period="H1"), inr("200", period="H2")], years=Decimal("0.5")
    )
    assert result.years == Decimal("0.5")


def test_unknown_metric() -> None:
    with pytest.raises(ValueError, match="median"):
        calculate("median", [inr("1")])


# --- AC #5: property test and edge values ---------------------------------------------------


def test_growth_property() -> None:
    """growth(a, a * (1 + g)) == g within 1e-20, for random a and g."""
    rng = random.Random(1004)  # noqa: S311 (reproducible test data)
    with localcontext(CONTEXT):
        for _ in range(2_000):
            a = Decimal(rng.randint(1, 10**15)) / Decimal(10 ** rng.randint(0, 6))
            if rng.random() < 0.3:
                a = -a
            g_ = Decimal(rng.randint(-99_999, 500_000)) / Decimal(10**5)
            b = a * (1 + g_) if a > 0 else a + abs(a) * g_
            result = calculate("growth", [CalcInput(a, Unit.PLAIN), CalcInput(b, Unit.PLAIN)])
            assert abs(result.value / 100 - g_) < Decimal("1e-20"), (a, g_)


def test_very_large_crore_amounts() -> None:
    result = calculate(
        "growth", [inr("9876543210.12", "lakh crore"), inr("9876543210.13", "lakh crore")]
    )
    assert result.value > 0
    assert result.display == "0.00%"


def test_tiny_percentages() -> None:
    result = calculate(
        "margin", [CalcInput(Decimal("1e-12"), Unit.PLAIN), CalcInput(Decimal(1), Unit.PLAIN)]
    )
    assert result.value == Decimal("1E-10")
    assert result.display == "0.00%"


def test_negative_growth_and_sum_of_negatives() -> None:
    assert calculate("growth", [inr("1250"), inr("1000")]).display == f"{MINUS}20.00%"
    assert calculate("sum", [inr("-1"), inr("-2")]).display == f"{MINUS}₹3.00"


def test_mean_of_one_input() -> None:
    result = calculate("mean", [inr("4215.5", "crore")])
    assert result.display == "₹4,215.50 crore"
    assert result.value == Decimal("42155000000.0")


def test_module_is_pure() -> None:
    tree = ast.parse(Path(calc_module.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0] if isinstance(node, ast.Import) else (node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert not {m for m in imported if m.split(".")[0] in {"logging", "os", "io", "pathlib",
                "structlog", "fastapi", "sqlalchemy", "math"}}  # fmt: skip
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called & {"float", "open", "eval", "exec", "print"}
