"""Figure extraction: one regex grammar over the text, left to right.

Alternatives are tried in this order at each position (the leftmost match wins, then
the first alternative): skipped spans (``cite:`` targets, URLs), locators (pages,
slides, sections, cells, footnotes), dates, period labels, ordinals, list markers,
and finally amounts (currency, numeral or range, scale word, unit).

Conventions (see :class:`.types.Unit` for values):

- Indian (``1,00,000``) and international (``100,000``) grouping both parse.
- A minus written directly before the number (``-5``, or a Unicode minus or en dash) makes a figure
  ``signed``: it then only matches negative values. Accounting parentheses around a
  bare number (``(1,234)``) make the value negative but not ``signed``: prose uses
  parentheses for asides too, so they match either sign. Parentheses around an amount
  with a currency or unit (``(US$ 505 mn)``) are read as an aside.
- A range (``10-12%`` with a hyphen or en dash, ``₹10-12 cr``, ``5 to 6x``) gives one
  figure per end; the scale and unit written after the range apply to both, and both
  share ``range_span``.
- Digits glued to letters (``INE002A01018``, ``EBITDA2``) and version-like strings
  (``3.2.1``) are not figures.
"""

import re
from collections.abc import Callable
from decimal import Decimal
from typing import Final

from app.guardrails.grounding.normalise import (
    currency_unit,
    date_step,
    encode_date,
    full_year,
    month_number,
    parse_numeral,
    scale_factor,
)
from app.guardrails.grounding.types import Figure, FigureKind, Unit

_NUM: Final = (
    r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,2}(?:,[0-9]{2})+,[0-9]{3}|[0-9]+)(?:\.[0-9]+)?|\.[0-9]+"
)
# A numeral ends cleanly: no more digits, grouping or decimals follow.
_NUM_END: Final = r"(?![0-9])(?!,[0-9]{3}(?![0-9]))(?!\.[0-9])"
_MONTH: Final = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?![a-z])\.?"
)
_YEAR4: Final = r"(?:19|20)[0-9]{2}"
_DASH: Final = r"[-\u2010\u2011\u2012\u2013\u2014\u2212]"
_APOS: Final = r"['\u2019]"

# Alternatives that may start anywhere: list markers (at a line start) and footnotes.
_ANYWHERE: Final = rf"""
(?P<lm>^[ \t]*(?:[-*+\u2022][ \t]+)?\(?(?P<lm_n>[0-9]{{1,2}})[.)](?=[ \t]))
|(?P<footnote>\[[0-9]{{1,3}}(?:\s?(?:,|{_DASH})\s?[0-9]{{1,3}})*\])
"""
# Everything else starts a token: one shared look-behind rejects mid-word positions
# before any alternative is tried (several times faster than per-alternative checks).
_TOKEN: Final = rf"""
(?P<skip>cite:[^\s)\]>]+|https?://[^\s)\]>]+|www\.[^\s)\]>]+)
|(?P<loc>(?:pp?\.|pp?(?=\s)|pages?|pgs?\.?|slides?|sections?|sec\.|§§?|clauses?
    |paras?\.?|paragraphs?)\s?[0-9]+(?:\.[0-9]+)*[a-z]?
    (?:\s?(?:{_DASH}|to|and|&)\s?[0-9]+(?:\.[0-9]+)*[a-z]?)*(?![0-9a-z]))
|(?P<cell>(?:'[^'\n]{{1,64}}'|[a-z0-9_]{{1,64}})!\$?[a-z]{{1,3}}\$?[0-9]+(?::\$?[a-z]{{1,3}}\$?[0-9]+)?
    |\$?[a-z]{{1,3}}\$?[0-9]+:\$?[a-z]{{1,3}}\$?[0-9]+(?![a-z0-9]))
|(?P<iso>(?P<iso_y>{_YEAR4})-(?P<iso_m>[0-9]{{2}})-(?P<iso_d>[0-9]{{2}})(?![0-9]))
|(?P<dmon>(?<![.,])(?P<dmon_d>[0-3]?[0-9])(?:st|nd|rd|th)?[\s\-/.]*(?P<dmon_m>{_MONTH})
    (?:[\s\-/.,]*(?P<dmon_y>{_YEAR4})|(?:[-/.]|{_APOS})(?P<dmon_yy>[0-9]{{2}}))(?![0-9]))
|(?P<mond>(?P<mond_m>{_MONTH})\s?(?P<mond_d>[0-3]?[0-9])(?:st|nd|rd|th)?,?\s+
    (?P<mond_y>{_YEAR4})(?![0-9]))
|(?P<ndate>(?<![.,/-])(?P<nd_a>[0-3]?[0-9])(?P<nd_sep>[/.\-])(?P<nd_b>[0-3]?[0-9])(?P=nd_sep)
    (?P<nd_y>{_YEAR4}|[0-9]{{2}})(?![0-9]|[/.\-][0-9]))
|(?P<mony>(?P<mony_m>{_MONTH})(?:[\s,]+(?P<mony_y>{_YEAR4})
    |\s?(?:{_DASH}|{_APOS})\s?(?P<mony_yy>[0-9]{{2}}))(?![0-9]))
|(?P<fy>(?:(?:q[1-4]|h[12]|[1-4]q|[12]h|[1-9]m)\s?)?(?:fy|cy)\s?{_APOS}?
    (?P<fy_y>{_YEAR4}|[0-9]{{2}})(?:\s?(?:{_DASH}|/)\s?(?:fy|cy)?\s?{_APOS}?(?P<fy_y2>{_YEAR4}|[0-9]{{2}}))?
    (?:[eapf](?![a-z0-9]))?(?![0-9a-z]))
|(?P<qy>(?:q[1-4]|h[12]|[1-4]q|[12]h)(?:\s?{_APOS}?(?P<qy_y>{_YEAR4})|{_APOS}?(?P<qy_yy>[0-9]{{2}}))?
    (?![0-9a-z]))
|(?P<yr>(?<![.,])(?P<yr_a>{_YEAR4})\s?(?P<yr_sep>{_DASH}|/|to)\s?(?P<yr_b>{_YEAR4}|[0-9]{{2}})
    (?![0-9]|[-/][0-9]))
|(?P<dur>(?<![.,])(?P<dur_n>[0-9]{{1,3}})(?:\s?{_DASH}\s?|\s)?(?:years?|yrs?|y)(?![a-z0-9]))
|(?P<ord>(?<![.,])(?P<ord_n>[0-9]{{1,4}})(?:st|nd|rd|th)(?![a-z0-9]))
|(?P<amt>(?<![0-9]\.)(?<![a-z]-)
    (?P<a_open>\()?
    (?P<a_neg>[-\u2212\u2013](?=[0-9.₹$€]|rs|inr|usd|us\$|eur))?
    (?:(?P<a_cur>₹|rs\.?|inr|us\$|usd|\$|€|eur)(?![a-z])\s?)?
    (?P<a_neg2>[-\u2212])?
    (?P<a_n1>{_NUM}){_NUM_END}
    (?:\s?(?P<a_rsep>{_DASH}|to)\s?(?P<a_n2>{_NUM}){_NUM_END})?
    (?:\s?(?P<a_scale>lakh\s+crores?|lakhs?|lacs?|crores?|cr|mn|millions?|bn|billions?|thousands?
        |trillions?|tn)(?![a-z])
      |(?P<a_gscale>mm|k|m|b)(?![a-z0-9]))?
    (?:\s?(?P<a_curp>inr|usd|eur|rupees?|dollars?|euros?)(?![a-z]))?
    (?:\s?(?P<a_unit>%|per\s?cent(?![a-z])|pct(?![a-z])|bps(?![a-z])|bp(?![a-z])
        |basis\s+points?(?![a-z]))
      |(?P<a_mult>x)(?![a-z0-9])
      |\s(?P<a_times>times)(?![a-z]))?
    (?P<a_close>\))?
    (?![a-z0-9_]))
"""
_FLAGS: Final = re.IGNORECASE | re.MULTILINE | re.VERBOSE
GRAMMAR: Final = re.compile(rf"{_ANYWHERE}|(?<![a-z0-9_])(?:{_TOKEN})", _FLAGS)
_AMOUNT_ONLY: Final = re.compile(rf"(?<![a-z0-9_]){_TOKEN[_TOKEN.index('(?P<amt>') :]}", _FLAGS)
_FIRST_NUMBER: Final = re.compile(r"[0-9]+(?:\.[0-9]+)?")

_UNIT_WORDS: Final = {
    "%": Unit.PCT,
    "percent": Unit.PCT,
    "per cent": Unit.PCT,
    "pct": Unit.PCT,
    "bps": Unit.BPS,
    "bp": Unit.BPS,
    "basis point": Unit.BPS,
    "basis points": Unit.BPS,
}
_YEAR_RANGE: Final = range(1900, 2100)


def extract_figures(text: str) -> list[Figure]:
    """Every figure in ``text``, in order of position."""
    figures: list[Figure] = []
    for m in GRAMMAR.finditer(text):
        handler = _HANDLERS.get(m.lastgroup or "")
        if handler is None:
            continue
        try:
            figures.extend(handler(m))
        except (ValueError, ArithmeticError):
            # Not a real date or period (31 Feb, month 13): read the digits as amounts.
            figures.extend(_amounts_in(text, m.start(), m.end()))
    return figures


def _amounts_in(text: str, start: int, end: int) -> list[Figure]:
    return [f for m in _AMOUNT_ONLY.finditer(text, start, end) for f in _amount(m)]


def _figure(
    m: re.Match[str],
    value: Decimal | int,
    unit: Unit,
    kind: FigureKind,
    *,
    step: Decimal | int = 1,
    group: str | None = None,
) -> Figure:
    start, end = m.span(group) if group else m.span()
    return Figure(
        span=(start, end),
        raw=m.string[start:end],
        value=Decimal(value),
        unit=unit,
        kind=kind,
        step=Decimal(step),
        number=Decimal(value) if unit is not Unit.DATE else None,
    )


def _date_figure(m: re.Match[str], year: int, month: int = 0, day: int = 0) -> list[Figure]:
    value = encode_date(year, month, day)
    kind = FigureKind.DATE if month else FigureKind.YEAR
    return [_figure(m, value, Unit.DATE, kind, step=date_step(value))]


def _period(m: re.Match[str], year: int | None) -> list[Figure]:
    value = encode_date(year) if year is not None else Decimal(0)
    return [_figure(m, value, Unit.DATE, FigureKind.PERIOD, step=10000)]


# --- Handlers -------------------------------------------------------------------------


def _skip(m: re.Match[str]) -> list[Figure]:
    return []


def _locator(m: re.Match[str]) -> list[Figure]:
    first = _FIRST_NUMBER.search(m.group())
    value = Decimal(first.group()) if first else Decimal(0)
    return [_figure(m, value, Unit.PLAIN, FigureKind.LOCATOR)]


def _iso(m: re.Match[str]) -> list[Figure]:
    return _date_figure(m, int(m["iso_y"]), int(m["iso_m"]), int(m["iso_d"]))


def _dmon(m: re.Match[str]) -> list[Figure]:
    year = full_year(m["dmon_y"] or m["dmon_yy"])
    return _date_figure(m, year, month_number(m["dmon_m"]), int(m["dmon_d"]))


def _mond(m: re.Match[str]) -> list[Figure]:
    return _date_figure(m, int(m["mond_y"]), month_number(m["mond_m"]), int(m["mond_d"]))


def _ndate(m: re.Match[str]) -> list[Figure]:
    a, b = int(m["nd_a"]), int(m["nd_b"])
    day, month = (b, a) if b > 12 >= a else (a, b)  # noqa: PLR2004  (DD/MM unless impossible)
    return _date_figure(m, full_year(m["nd_y"]), month, day)


def _mony(m: re.Match[str]) -> list[Figure]:
    return _date_figure(m, full_year(m["mony_y"] or m["mony_yy"]), month_number(m["mony_m"]))


def _fy(m: re.Match[str]) -> list[Figure]:
    start = full_year(m["fy_y"])
    end = _second_year(start, m["fy_y2"]) if m["fy_y2"] else start
    return _period(m, end)


def _qy(m: re.Match[str]) -> list[Figure]:
    year = m["qy_y"] or m["qy_yy"]
    return _period(m, full_year(year) if year else None)


def _second_year(first: int, written: str) -> int:
    if len(written) == 4:  # noqa: PLR2004
        second = int(written)
    else:
        second = first // 100 * 100 + int(written)
        if second < first:
            second += 100
    if second < first:
        raise ValueError("range runs backwards")
    return second


def _year_range(m: re.Match[str]) -> list[Figure]:
    first, written, sep = int(m["yr_a"]), m["yr_b"], m["yr_sep"]
    if len(written) == 2 and sep in "-/" and 1 <= int(written) <= 12:  # noqa: PLR2004
        candidate = first // 100 * 100 + int(written)
        if candidate != first + 1 and candidate < first:
            # "2025-03" is March 2025; "2011-12" stays the period FY 2011-12.
            return _date_figure(m, first, int(written))
    return _period(m, _second_year(first, written))


def _duration(m: re.Match[str]) -> list[Figure]:
    return [_figure(m, int(m["dur_n"]), Unit.COUNT, FigureKind.PERIOD)]


def _ordinal(m: re.Match[str]) -> list[Figure]:
    return [_figure(m, int(m["ord_n"]), Unit.COUNT, FigureKind.ORDINAL)]


def _list_marker(m: re.Match[str]) -> list[Figure]:
    return [_figure(m, int(m["lm_n"]), Unit.COUNT, FigureKind.LIST_MARKER, group="lm_n")]


def _amount(m: re.Match[str]) -> list[Figure]:  # noqa: PLR0912
    start, end = m.span()
    paren = bool(m["a_open"] and m["a_close"])
    if m["a_open"] and not paren:
        start += 1
    if m["a_close"] and not paren:
        end -= 1

    currency = currency_unit(m["a_cur"]) or currency_unit(m["a_curp"])
    unit_word = m["a_unit"]
    unit: Unit | None
    if unit_word:
        unit = _UNIT_WORDS[" ".join(unit_word.lower().split())]
    elif m["a_mult"] or m["a_times"]:
        unit = Unit.MULTIPLE
    elif currency:
        unit = Unit(currency)
    else:
        unit = None
    scale_word = m["a_scale"] or m["a_gscale"]
    factor = scale_factor(scale_word)
    minus = bool(m["a_neg"] or m["a_neg2"])
    # Accounting negative: parentheses around a bare number (optionally scaled).
    accounting = paren and unit is None and not minus
    if paren and not accounting:
        start, end = start + 1, end - 1  # an aside: the parentheses aren't part of it
    is_range = m["a_n2"] is not None

    ends = [("a_n1", m["a_n1"])] + ([("a_n2", m["a_n2"])] if is_range else [])
    figures: list[Figure] = []
    for i, (group, numeral) in enumerate(ends):
        number, decimals = parse_numeral(numeral)
        if (minus and i == 0) or accounting:
            number = -number
        value = number * factor
        step = Decimal(1).scaleb(-decimals) * factor
        kind = FigureKind.NUMBER
        if unit is not None:
            figure_unit = unit
        elif (
            not is_range
            and scale_word is None
            and not (minus or paren)
            and decimals == 0
            and "," not in numeral
            and len(numeral) == 4  # noqa: PLR2004
            and int(numeral) in _YEAR_RANGE
        ):
            figure_unit, kind = Unit.DATE, FigureKind.YEAR
            value, step = encode_date(int(numeral)), date_step(encode_date(int(numeral)))
        elif scale_word is None and decimals == 0 and number >= 0:
            figure_unit = Unit.COUNT
        else:
            figure_unit = Unit.PLAIN

        if is_range:
            span = (m.start(group), m.end(group)) if i == 0 else (m.start(group), end)
        else:
            span = (start, end)
        figures.append(
            Figure(
                span=span,
                raw=m.string[span[0] : span[1]],
                value=value,
                unit=figure_unit,
                kind=kind,
                step=step,
                number=number,
                signed=minus and i == 0,
                range_span=(start, end) if is_range else None,
            )
        )
    return figures


_HANDLERS: Final[dict[str, Callable[[re.Match[str]], list[Figure]]]] = {
    "skip": _skip,
    "loc": _locator,
    "cell": _locator,
    "footnote": _locator,
    "iso": _iso,
    "dmon": _dmon,
    "mond": _mond,
    "ndate": _ndate,
    "mony": _mony,
    "fy": _fy,
    "qy": _qy,
    "yr": _year_range,
    "dur": _duration,
    "ord": _ordinal,
    "lm": _list_marker,
    "amt": _amount,
}
