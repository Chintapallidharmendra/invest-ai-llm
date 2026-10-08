"""Grounding sets, matching and exemptions (FR-036, ADR-025).

Matching rules:

- Values match within one dimension only: an amount in one currency, ratios (PCT and
  BPS, converted), multiples, plain numbers, dates. Cross-currency never matches.
- A figure matches a source value that rounds to it at the figure's display step:
  "4,215 cr" covers [4,214.5, 4,215.5) crore, so it matches 4,215.37 cr but "4,200 cr"
  does not. A more precise figure than its source ("4,215.37" vs "4,215") does not match.
- A figure without an explicit minus matches either sign ("a loss of ₹120 cr" against a
  source "(120)"); a ``signed`` figure matches only same-sign values.
- A unit-less figure also matches the numeral a source wrote with a unit or scale ("the
  4,215 figure" against "₹4,215 cr"), and a scaled source amount ("42.15 bn" against
  "₹4,215 crore"). An amount with a currency must match an amount in that currency.
- A date matches at its own precision: "2025" matches any 2025 date or FY2025 label;
  "March 2025" any day in it; "31 March 2025" only that day.
"""

import re
from collections.abc import Iterable
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Final

from app.guardrails.grounding.extract import extract_figures
from app.guardrails.grounding.normalise import (
    CURRENCIES,
    date_precision,
    dimension_of,
    encode_date,
    scale_factor,
    to_key,
    truncate_date,
)
from app.guardrails.grounding.types import (
    ExplicitValue,
    Figure,
    FigureKind,
    GroundedValue,
    GroundingSet,
    MatchReason,
    MatchResult,
    Source,
    SourceText,
    TableCell,
    Unit,
)

_TWO: Final = Decimal(2)
_SMALL_COUNT: Final = 12
_EXEMPT_KINDS: Final = frozenset({FigureKind.PERIOD, FigureKind.LOCATOR, FigureKind.LIST_MARKER})
_NOT_GROUNDING: Final = frozenset({FigureKind.LOCATOR, FigureKind.LIST_MARKER})
_UNIT_LESS: Final = frozenset({Unit.COUNT, Unit.PLAIN})


# --- Building -------------------------------------------------------------------------


def build_grounding_set(sources: Iterable[Source]) -> GroundingSet:
    """Canonical values (with origins) from source texts, table cells and explicit values.

    Locators and list markers in sources ground nothing; period labels ground their year.
    """
    entries: list[GroundedValue] = []
    for source in sources:
        if isinstance(source, SourceText):
            for figure in extract_figures(source.text):
                entries.extend(_entries(figure, source.origin))
        elif isinstance(source, TableCell):
            for figure in _cell_figures(source):
                entries.extend(_entries(figure, source.origin))
        else:
            entries.extend(_explicit(source))
    return GroundingSet(entries)


def _entries(figure: Figure, origin: str) -> list[GroundedValue]:
    if figure.kind in _NOT_GROUNDING:
        return []
    raw = figure.raw
    if figure.unit is Unit.DATE:
        if figure.kind is FigureKind.PERIOD and figure.value == 0:
            return []  # "H1", "Q3": no year to ground
        # One entry per precision the date has: year, month, day.
        precisions = {"Y": ("Y",), "M": ("Y", "M"), "D": ("Y", "M", "D")}
        return [
            GroundedValue(truncate_date(figure.value, p), Unit.DATE, origin, raw)
            for p in precisions[date_precision(figure.value)]
        ]
    entries = [GroundedValue(figure.value, figure.unit, origin, raw)]
    if figure.unit in (Unit.INR, Unit.USD, Unit.EUR):
        entries.append(GroundedValue(figure.value, Unit.PLAIN, origin, raw))
    if figure.number is not None and (
        figure.unit not in _UNIT_LESS or figure.number != figure.value
    ):
        entries.append(GroundedValue(figure.number, Unit.PLAIN, origin, raw))
    return entries


def _cell_figures(cell: TableCell) -> list[Figure]:
    """The cell's figures, with the table's unit and scale applied to bare numbers."""
    if isinstance(cell.value, str):
        figures = extract_figures(cell.value)
    else:
        number = Decimal(cell.value)
        exponent = number.as_tuple().exponent
        decimals = -exponent if isinstance(exponent, int) and exponent < 0 else 0
        figures = [
            Figure(
                span=(0, 0),
                raw=str(cell.value),
                value=number,
                unit=Unit.COUNT if decimals == 0 and number >= 0 else Unit.PLAIN,
                step=Decimal(1).scaleb(-decimals),
                number=number,
            )
        ]
    if cell.unit is None and cell.scale is None:
        return figures
    factor = cell.scale if isinstance(cell.scale, Decimal) else scale_factor(cell.scale)
    out = []
    for f in figures:
        if f.kind is not FigureKind.NUMBER or f.unit not in _UNIT_LESS or f.number is None:
            out.append(f)  # the cell states its own unit or scale
            continue
        unit = cell.unit or Unit.PLAIN
        scale = factor if unit not in (Unit.PCT, Unit.BPS, Unit.MULTIPLE) else Decimal(1)
        out.append(
            Figure(
                span=f.span,
                raw=f.raw,
                value=f.number * scale,
                unit=unit,
                step=f.step * scale,
                number=f.number,
                signed=f.signed,
            )
        )
    return out


def _explicit(source: ExplicitValue) -> list[GroundedValue]:
    value = source.value
    if isinstance(value, date):
        return _entries(
            Figure(
                (0, 0),
                value.isoformat(),
                encode_date(value.year, value.month, value.day),
                Unit.DATE,
                FigureKind.DATE,
            ),
            source.origin,
        )
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("explicit value is not a number") from None
    figure = Figure((0, 0), str(value), number, source.unit, number=number)
    return _entries(figure, source.origin)


# --- Matching -------------------------------------------------------------------------


def match(figure: Figure, grounding_set: GroundingSet) -> MatchResult:
    """Whether ``figure`` is in ``grounding_set`` within its display precision.

    Exemptions are not applied here; see :func:`is_exempt` and :func:`ground_text`.
    """
    found = _find(figure, figure.unit, grounding_set)
    if found:
        return MatchResult(figure, MatchReason.MATCHED, _origins(found))
    if figure.unit.value in CURRENCIES:
        for other in CURRENCIES - {figure.unit.value}:
            if _find(figure, Unit(other), grounding_set):
                return MatchResult(figure, MatchReason.CROSS_CURRENCY)
    return MatchResult(figure, MatchReason.NOT_FOUND)


def _find(figure: Figure, unit: Unit, grounding_set: GroundingSet) -> list[GroundedValue]:
    if unit is Unit.DATE:
        if figure.kind is FigureKind.PERIOD and figure.value == 0:
            return []
        dim = dimension_of(unit.value, figure.value)
        return grounding_set.find(dim, figure.value, figure.value, include_hi=True)

    dim = dimension_of(unit.value)
    value, half = to_key(unit.value, figure.value), to_key(unit.value, figure.step) / _TWO
    magnitude = abs(value)
    lo, hi = magnitude - half, magnitude + half
    found: list[GroundedValue] = []
    if not (figure.signed and value < 0):
        found += grounding_set.find(dim, lo, hi)  # [lo, hi)
    if not (figure.signed and value > 0):
        # Mirror image for negatives, rounding half away from zero: (-hi, -lo].
        found += [
            e
            for e in grounding_set.find(dim, -hi, -lo, include_lo=False, include_hi=True)
            if e not in found
        ]
    return found


def _origins(found: list[GroundedValue]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(e.origin for e in found))


# --- Exemptions -----------------------------------------------------------------------

_YEAR_BEFORE: Final = re.compile(
    r"(?:\bfiscal(?:\s+year)?|\bfinancial\s+year|\bcalendar(?:\s+year)?|\bfy|\bcy|\byear)"
    r"[\s:'\u2019]*$",
    re.IGNORECASE,
)
_YEAR_AFTER: Final = re.compile(
    r"\s*(?:fiscal|financial\s+year|calendar\s+year|fy\b|cy\b)", re.IGNORECASE
)
_CITATION_LINK: Final = re.compile(r"\[[^\]\n]*\]\(cite:[^)\s]*\)")
_CONTEXT_WINDOW: Final = 24


def is_exempt(figure: Figure, context: str = "") -> bool:
    """Whether ``figure`` needs no grounding.

    Exempt: period labels and durations ("FY26", "Q2 FY26", "H1", "1 year", "3Y",
    "2023-2026"); page/slide/section/cell/footnote locators; list markers; ordinals up to
    12th; counts up to 12 written without a unit ("3 deals"); a year inside a period
    phrase ("fiscal 2025", "calendar year 2024"); anything inside a ``[...](cite:...)``
    link. ``context`` is the text the figure was extracted from (spans index into it).
    """
    if figure.kind in _EXEMPT_KINDS:
        return True
    if figure.kind is FigureKind.ORDINAL:
        return figure.value <= _SMALL_COUNT
    if (
        figure.kind is FigureKind.NUMBER
        and figure.unit is Unit.COUNT
        and 0 <= figure.value <= _SMALL_COUNT
    ):
        return True
    if not context:
        return False
    start, end = figure.span
    if figure.kind is FigureKind.YEAR and (
        _YEAR_BEFORE.search(context, max(0, start - _CONTEXT_WINDOW), start)
        or _YEAR_AFTER.match(context, end, end + _CONTEXT_WINDOW)
    ):
        return True
    return any(lo <= start and end <= hi for lo, hi in _citation_links(context))


@lru_cache(maxsize=16)
def _citation_links(context: str) -> tuple[tuple[int, int], ...]:
    # Cached: ground_text asks once per figure of the same text.
    return tuple(m.span() for m in _CITATION_LINK.finditer(context))


def ground_text(text: str, grounding_set: GroundingSet) -> list[MatchResult]:
    """Every figure in ``text`` with its result: ``EXEMPT``, ``MATCHED``, ``NOT_FOUND`` or
    ``CROSS_CURRENCY``. Each end of a range is its own result."""
    return [
        MatchResult(f, MatchReason.EXEMPT) if is_exempt(f, text) else match(f, grounding_set)
        for f in extract_figures(text)
    ]
