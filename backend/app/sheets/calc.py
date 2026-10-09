"""Financial calculations in exact decimal arithmetic (Story 10.4; FR-023, ADR-030).

Every figure an answer computes comes from here, never from the model::

    result = calculate(
        Metric.CAGR,
        [
            CalcInput(Decimal("812.4"), Unit.INR, scale="crore", period="FY22", ref=ref_a),
            CalcInput(Decimal("1250.0"), Unit.INR, scale="crore", period="FY25", ref=ref_b),
        ],
    )
    result.display   # "15.45%"
    result.formula   # "CAGR = (1,250.0 / 812.4)^(1/3) - 1 = 15.45%" (with a typographic minus)
    result.as_grounding_value()  # an ExplicitValue for the output gate (4.5, 9.10)

Inputs follow the grounding conventions (4.4): ``value`` is the number as written and
``scale`` its scale word ("crore", "lakh") or factor, so money is compared in base units;
PCT is percent; MULTIPLE is as written. All arithmetic runs in :data:`CONTEXT` (34
significant digits); floats are refused. Results keep full precision in ``value`` and
are shown rounded half-up to 2 decimals.

CAGR needs a fractional power, which ``Decimal`` lacks: it is computed as
``exp(ln(end / start) / years)`` in the same context, accurate to about 30 significant
digits, far beyond the 2 decimals shown.

Pure: no I/O, logging or settings.
"""

import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import (
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from enum import StrEnum
from typing import Final

from app.guardrails.grounding import ExplicitValue, Unit
from app.guardrails.grounding.normalise import SCALES, scale_factor

CONTEXT: Final = Context(
    prec=34, rounding=ROUND_HALF_EVEN, traps=[InvalidOperation, DivisionByZero, Overflow]
)
DISPLAY_STEP: Final = Decimal("0.01")
_HUNDRED: Final = Decimal(100)
_MINUS: Final = chr(0x2212)  # the typographic minus used in formulas
_TIMES: Final = chr(0xD7)  # multiplication sign

_CURRENCIES: Final = frozenset({Unit.INR, Unit.USD, Unit.EUR})
_SYMBOLS: Final[Mapping[Unit, str]] = {Unit.INR: "₹", Unit.USD: "$", Unit.EUR: "€"}
# Units a calculation can't take: a date is not an amount.
_NOT_NUMERIC: Final = frozenset({Unit.DATE})
# Unit-less numbers combine with each other.
_UNITLESS: Final = frozenset({Unit.PLAIN, Unit.COUNT})

_YEAR_LABEL: Final = re.compile(r"(?:FY|CY)?\s*'?([0-9]{2}|[0-9]{4})", re.IGNORECASE)


class Metric(StrEnum):
    GROWTH = "growth"  # (b - a) / |a|, %
    CAGR = "cagr"  # (end / start)^(1/years) - 1, %
    MARGIN = "margin"  # part / whole, %
    RATIO = "ratio"  # a / b
    MULTIPLE = "multiple"  # a / b, x (e.g. EV / EBITDA)
    SUM = "sum"
    MEAN = "mean"


class ErrorCode(StrEnum):
    ZERO_DENOMINATOR = "zero_denominator"
    NEGATIVE_BASE = "negative_base"
    MISMATCHED_UNITS = "mismatched_units"
    MISMATCHED_PERIODS = "mismatched_periods"
    TOO_FEW_INPUTS = "too_few_inputs"
    TOO_MANY_INPUTS = "too_many_inputs"
    INVALID_YEARS = "invalid_years"


class CalcError(ValueError):
    """A calculation that can't be done. ``message`` is user-facing and holds no data."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class InputRef:
    """Where an input came from: a document and a locator (``p.12``, ``Sheet1!C4``)."""

    document_id: uuid.UUID
    locator: str


@dataclass(frozen=True, slots=True)
class CalcInput:
    """One cited input. ``value`` is the number as written; ``scale`` a word or factor."""

    value: Decimal
    unit: Unit
    scale: str | Decimal | None = None
    period: str | None = None
    ref: InputRef | None = None

    def __post_init__(self) -> None:
        raw: object = self.value  # callers may pass an int or str too
        if isinstance(raw, float) or not isinstance(raw, Decimal | int | str):
            raise TypeError("CalcInput.value must be a Decimal, int or str, never a float")
        if not isinstance(raw, Decimal):
            object.__setattr__(self, "value", Decimal(raw))
        if not self.value.is_finite():
            raise ValueError("CalcInput.value must be finite")
        scale: object = self.scale
        if isinstance(scale, float):
            raise TypeError("CalcInput.scale must be a word or a Decimal, never a float")
        if isinstance(self.scale, str):
            scale_factor(self.scale)  # KeyError for an unknown word

    @property
    def factor(self) -> Decimal:
        if isinstance(self.scale, Decimal):
            return self.scale
        return scale_factor(self.scale)

    @property
    def canonical(self) -> Decimal:
        """The value with its scale applied (money in base units)."""
        with localcontext(CONTEXT):
            return self.value * self.factor


@dataclass(frozen=True, slots=True)
class CalcResult:
    metric: Metric
    value: Decimal  # full precision, canonical (money in base units, PCT in percent)
    unit: Unit
    display_value: Decimal  # rounded half-up to 2 decimals, in the display scale
    display: str  # "15.45%", "12.40x", "₹5,215.00 crore"
    formula: str  # the formula with the inputs substituted
    inputs: tuple[CalcInput, ...]
    years: Decimal | None = None
    scale: str | None = field(default=None)  # the scale word of display_value, if any

    def as_grounding_value(self, origin: str | None = None) -> ExplicitValue:
        """The result for the grounding set (4.4), at full precision, so any answer
        figure that rounds to it at the precision the answer shows is matched."""
        return ExplicitValue(self.value, self.unit, origin or f"calc:{self.metric.value}")


# --- Public API ------------------------------------------------------------------------


def calculate(
    metric: Metric | str,
    inputs: Sequence[CalcInput],
    *,
    years: Decimal | int | str | None = None,
) -> CalcResult:
    """Compute ``metric`` over ``inputs``; raises :class:`CalcError` for invalid input.

    Two inputs for ``growth``/``cagr`` (earlier, later), ``margin`` (part, whole),
    ``ratio``/``multiple`` (numerator, denominator); one or more for ``sum``/``mean``.
    ``years`` is required for ``cagr`` unless both period labels are years (``FY22``,
    ``FY2025``, ``2024``), from which it is inferred.
    """
    metric = Metric(metric)
    inputs = tuple(inputs)
    with localcontext(CONTEXT):
        return _METRICS[metric](inputs, _years(years))


# --- Metrics ---------------------------------------------------------------------------


def _growth(inputs: tuple[CalcInput, ...], _: Decimal | None) -> CalcResult:
    start, end = _exactly_two(inputs)
    _same_unit(start, end)
    _different_periods(start, end)
    if start.canonical == 0:
        raise CalcError(
            ErrorCode.ZERO_DENOMINATOR, "The starting value is zero, so growth is undefined."
        )
    value = (end.canonical - start.canonical) / abs(start.canonical) * _HUNDRED
    a, b = _operands(inputs)
    base = a[1:-1] if a.startswith("(") else a  # |-200|, not |(-200)|
    formula = f"Growth = ({b} {_MINUS} {a}) / |{base}|"
    return _result(Metric.GROWTH, value, Unit.PCT, inputs, formula)


def _cagr(inputs: tuple[CalcInput, ...], years: Decimal | None) -> CalcResult:
    start, end = _exactly_two(inputs)
    _same_unit(start, end)
    years = _cagr_years(start, end, years)
    if start.canonical <= 0 or end.canonical < 0:
        raise CalcError(
            ErrorCode.NEGATIVE_BASE,
            "CAGR needs a positive starting value and an ending value that isn't negative.",
        )
    ratio = end.canonical / start.canonical
    # An ending value of zero is -100% a year (ln(0) is undefined).
    value = -_HUNDRED if ratio == 0 else ((ratio.ln() / years).exp() - 1) * _HUNDRED
    a, b = _operands(inputs)
    return _result(
        Metric.CAGR,
        value,
        Unit.PCT,
        inputs,
        f"CAGR = ({b} / {a})^(1/{_plain(years)}) {_MINUS} 1",
        years=years,
    )


def _divide(metric: Metric, unit: Unit, symbol: str) -> Callable[..., CalcResult]:
    def compute(inputs: tuple[CalcInput, ...], _: Decimal | None) -> CalcResult:
        numerator, denominator = _exactly_two(inputs)
        _same_unit(numerator, denominator)
        _same_period(numerator, denominator)
        if denominator.canonical == 0:
            raise CalcError(
                ErrorCode.ZERO_DENOMINATOR,
                f"The {_DENOMINATOR_NAMES[metric]} is zero, so the {metric.value} is undefined.",
            )
        value = numerator.canonical / denominator.canonical
        if unit is Unit.PCT:
            value *= _HUNDRED
        a, b = _operands(inputs)
        return _result(metric, value, unit, inputs, f"{symbol} = {a} / {b}")

    return compute


_DENOMINATOR_NAMES: Final = {
    Metric.MARGIN: "whole",
    Metric.RATIO: "denominator",
    Metric.MULTIPLE: "denominator",
}


def _aggregate(metric: Metric) -> Callable[..., CalcResult]:
    def compute(inputs: tuple[CalcInput, ...], _: Decimal | None) -> CalcResult:
        if not inputs:
            raise CalcError(ErrorCode.TOO_FEW_INPUTS, f"A {metric.value} needs at least one value.")
        for other in inputs[1:]:
            _same_unit(inputs[0], other)
        unit = _common_unit(inputs)
        total = sum((i.canonical for i in inputs), Decimal(0))
        terms = " + ".join(_operands(inputs))
        if metric is Metric.SUM:
            return _result(metric, total, unit, inputs, f"Sum = {terms}")
        value = total / len(inputs)
        return _result(metric, value, unit, inputs, f"Mean = ({terms}) / {len(inputs)}")

    return compute


_METRICS: Final[Mapping[Metric, Callable[[tuple[CalcInput, ...], Decimal | None], CalcResult]]] = {
    Metric.GROWTH: _growth,
    Metric.CAGR: _cagr,
    Metric.MARGIN: _divide(Metric.MARGIN, Unit.PCT, "Margin"),
    Metric.RATIO: _divide(Metric.RATIO, Unit.PLAIN, "Ratio"),
    Metric.MULTIPLE: _divide(Metric.MULTIPLE, Unit.MULTIPLE, "Multiple"),
    Metric.SUM: _aggregate(Metric.SUM),
    Metric.MEAN: _aggregate(Metric.MEAN),
}


# --- Validation ------------------------------------------------------------------------


def _exactly_two(inputs: tuple[CalcInput, ...]) -> tuple[CalcInput, CalcInput]:
    if len(inputs) < 2:  # noqa: PLR2004
        raise CalcError(ErrorCode.TOO_FEW_INPUTS, "This calculation needs two values.")
    if len(inputs) > 2:  # noqa: PLR2004
        raise CalcError(ErrorCode.TOO_MANY_INPUTS, "This calculation takes exactly two values.")
    return inputs[0], inputs[1]


def _same_unit(a: CalcInput, b: CalcInput) -> None:
    for side in (a, b):
        if side.unit in _NOT_NUMERIC:
            raise CalcError(ErrorCode.MISMATCHED_UNITS, "A date can't be used in a calculation.")
    if a.unit == b.unit or {a.unit, b.unit} <= _UNITLESS:
        return
    if a.unit in _CURRENCIES and b.unit in _CURRENCIES:
        message = "The values are in different currencies."
    else:
        message = "The values are in different units."
    raise CalcError(ErrorCode.MISMATCHED_UNITS, message)


def _common_unit(inputs: tuple[CalcInput, ...]) -> Unit:
    units = {i.unit for i in inputs}
    return units.pop() if len(units) == 1 else Unit.PLAIN  # PLAIN and COUNT mixed


def _different_periods(a: CalcInput, b: CalcInput) -> None:
    if a.period and b.period and _fold(a.period) == _fold(b.period):
        raise CalcError(ErrorCode.MISMATCHED_PERIODS, "Both values are for the same period.")


def _same_period(a: CalcInput, b: CalcInput) -> None:
    if a.period and b.period and _fold(a.period) != _fold(b.period):
        raise CalcError(ErrorCode.MISMATCHED_PERIODS, "The two values are for different periods.")


def _fold(label: str) -> str:
    return " ".join(label.casefold().split())


def _years(years: Decimal | int | str | None) -> Decimal | None:
    if years is None:
        return None
    if isinstance(years, float) or not isinstance(years, Decimal | int | str):
        raise TypeError("years must be a Decimal, int or str, never a float")
    try:
        value = Decimal(years)
    except InvalidOperation:
        raise CalcError(ErrorCode.INVALID_YEARS, "The number of years isn't valid.") from None
    if not value.is_finite() or value <= 0:
        raise CalcError(ErrorCode.INVALID_YEARS, "The number of years must be more than zero.")
    return value


def _label_year(label: str | None) -> int | None:
    if not label:
        return None
    found = _YEAR_LABEL.fullmatch(label.strip())
    if found is None:
        return None
    year = int(found.group(1))
    return year + 2000 if year < 100 else year  # noqa: PLR2004 (two-digit years)


def _cagr_years(start: CalcInput, end: CalcInput, years: Decimal | None) -> Decimal:
    _different_periods(start, end)
    first, last = _label_year(start.period), _label_year(end.period)
    inferred = Decimal(last - first) if first is not None and last is not None else None
    if inferred is not None and inferred <= 0:
        raise CalcError(
            ErrorCode.MISMATCHED_PERIODS, "The ending period must come after the starting one."
        )
    if years is None:
        if inferred is None:
            raise CalcError(ErrorCode.INVALID_YEARS, "CAGR needs the number of years.")
        return inferred
    if inferred is not None and inferred != years:
        raise CalcError(
            ErrorCode.MISMATCHED_PERIODS,
            "The number of years doesn't match the two periods.",
        )
    return years


# --- Formatting ------------------------------------------------------------------------


def _plain(number: Decimal) -> str:
    """A number as written, with thousands separators: 1250.0 -> "1,250.0"."""
    text = f"{number:,f}"
    return text


def _operand(item: CalcInput, show_scale: bool) -> str:
    text = _plain(item.value)
    if item.value < 0:
        text = f"({_MINUS}{_plain(-item.value)})"
    if show_scale and item.scale is not None:
        text = f"{text} {_scale_word(item)}"
    return text


def _scale_word(item: CalcInput) -> str:
    if isinstance(item.scale, str):
        return " ".join(item.scale.split())
    return f"{_TIMES} {_plain(item.factor)}"


def _operands(inputs: tuple[CalcInput, ...]) -> list[str]:
    # Scales are shown only when the inputs don't all share one (e.g. crore vs lakh).
    show_scale = len({i.factor for i in inputs}) > 1
    return [_operand(i, show_scale) for i in inputs]


def _shared_scale(inputs: tuple[CalcInput, ...]) -> tuple[str | None, Decimal]:
    words = {_fold(i.scale) if isinstance(i.scale, str) else i.scale for i in inputs}
    if len(words) == 1:
        (word,) = words
        if isinstance(word, str) and word in SCALES:
            return word, SCALES[word]
    return None, Decimal(1)


def _round(value: Decimal) -> Decimal:
    return value.quantize(DISPLAY_STEP, rounding=ROUND_HALF_UP)


def _show(display_value: Decimal, unit: Unit, scale: str | None) -> str:
    number = f"{display_value:,f}"
    if display_value < 0:
        number = f"{_MINUS}{-display_value:,f}"
    if unit is Unit.PCT:
        return f"{number}%"
    if unit is Unit.BPS:
        return f"{number} bps"
    if unit is Unit.MULTIPLE:
        return f"{number}x"
    if unit in _SYMBOLS:
        sign, digits = (number[0], number[1:]) if number.startswith(_MINUS) else ("", number)
        return f"{sign}{_SYMBOLS[unit]}{digits}" + (f" {scale}" if scale else "")
    return number + (f" {scale}" if scale else "")


def _result(
    metric: Metric,
    value: Decimal,
    unit: Unit,
    inputs: tuple[CalcInput, ...],
    formula: str,
    *,
    years: Decimal | None = None,
) -> CalcResult:
    scale_word: str | None = None
    divisor = Decimal(1)
    if metric in {Metric.SUM, Metric.MEAN}:
        # Sums and means of amounts are shown in the inputs' shared scale ("crore").
        scale_word, divisor = _shared_scale(inputs)
    value = +value  # apply the context's precision
    display_value = _round(value / divisor)
    display = _show(display_value, unit, scale_word)
    return CalcResult(
        metric=metric,
        value=value,
        unit=unit,
        display_value=display_value,
        display=display,
        formula=f"{formula} = {display}",
        inputs=inputs,
        years=years,
        scale=scale_word,
    )
