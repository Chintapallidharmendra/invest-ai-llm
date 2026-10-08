"""Canonicalisation: numerals, scale words, currencies, dates and comparison keys.

Units are passed by value (``"INR"``, ``"BPS"``…) so this module sits below
:mod:`.types` without importing it.
"""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Final

# Comparison dimension: values only ever match within one dimension.
#   ("MONEY", currency) · ("RATIO", "") (PCT and BPS, in percent) · ("MULTIPLE", "")
#   ("NUMBER", "") (COUNT and PLAIN) · ("DATE", "Y"|"M"|"D") · ("ORDINAL", "")
Dimension = tuple[str, str]

CURRENCIES: Final = frozenset({"INR", "USD", "EUR"})
_HUNDRED: Final = Decimal(100)

SCALES: Final[Mapping[str, Decimal]] = MappingProxyType(
    {
        "k": Decimal(10) ** 3,
        "thousand": Decimal(10) ** 3,
        "thousands": Decimal(10) ** 3,
        "lakh": Decimal(10) ** 5,
        "lakhs": Decimal(10) ** 5,
        "lac": Decimal(10) ** 5,
        "lacs": Decimal(10) ** 5,
        "m": Decimal(10) ** 6,
        "mm": Decimal(10) ** 6,
        "mn": Decimal(10) ** 6,
        "million": Decimal(10) ** 6,
        "millions": Decimal(10) ** 6,
        "cr": Decimal(10) ** 7,
        "crore": Decimal(10) ** 7,
        "crores": Decimal(10) ** 7,
        "b": Decimal(10) ** 9,
        "bn": Decimal(10) ** 9,
        "billion": Decimal(10) ** 9,
        "billions": Decimal(10) ** 9,
        "tn": Decimal(10) ** 12,
        "trillion": Decimal(10) ** 12,
        "trillions": Decimal(10) ** 12,
        "lakh crore": Decimal(10) ** 12,
        "lakh crores": Decimal(10) ** 12,
    }
)

CURRENCY_TOKENS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "₹": "INR",
        "rs": "INR",
        "rs.": "INR",
        "inr": "INR",
        "rupee": "INR",
        "rupees": "INR",
        "$": "USD",
        "us$": "USD",
        "usd": "USD",
        "dollar": "USD",
        "dollars": "USD",
        "€": "EUR",
        "eur": "EUR",
        "euro": "EUR",
        "euros": "EUR",
    }
)

MONTHS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
)


def parse_numeral(numeral: str) -> tuple[Decimal, int]:
    """``"1,00,000.50"`` -> ``(Decimal("100000.50"), 2)``: value and decimals shown."""
    plain = numeral.replace(",", "")
    decimals = len(plain) - plain.index(".") - 1 if "." in plain else 0
    return Decimal(plain), decimals


def scale_factor(word: str | None) -> Decimal:
    if not word:
        return Decimal(1)
    return SCALES[" ".join(word.lower().rstrip(".").split())]


def currency_unit(token: str | None) -> str | None:
    if not token:
        return None
    return CURRENCY_TOKENS[token.lower().replace(" ", "")]


def month_number(name: str) -> int:
    return MONTHS[name.lower().rstrip(".")]


def full_year(digits: str) -> int:
    """Four digits as written; two digits as 20YY (finance texts are this century)."""
    year = int(digits.lstrip("'\u2019"))
    return year if year >= 100 else 2000 + year  # noqa: PLR2004


# --- Dates as numbers: YYYYMMDD, with 00 for parts not written --------------------------


def encode_date(year: int, month: int = 0, day: int = 0) -> Decimal:
    """Validates real calendar dates (``ValueError`` otherwise)."""
    if month and day:
        date(year, month, day)
    elif month and not 1 <= month <= 12:  # noqa: PLR2004
        raise ValueError("month out of range")
    return Decimal(year * 10000 + month * 100 + day)


def date_parts(value: Decimal) -> tuple[int, int, int]:
    n = int(value)
    return n // 10000, n // 100 % 100, n % 100


def date_precision(value: Decimal) -> str:
    _, month, day = date_parts(value)
    return "D" if day else "M" if month else "Y"


def date_step(value: Decimal) -> Decimal:
    return {"D": Decimal(1), "M": Decimal(100), "Y": Decimal(10000)}[date_precision(value)]


def date_iso(value: Decimal) -> str:
    year, month, day = date_parts(value)
    if day:
        return f"{year:04d}-{month:02d}-{day:02d}"
    if month:
        return f"{year:04d}-{month:02d}"
    return f"{year:04d}"


def truncate_date(value: Decimal, precision: str) -> Decimal:
    year, month, _ = date_parts(value)
    if precision == "Y":
        return Decimal(year * 10000)
    if precision == "M":
        return Decimal(year * 10000 + month * 100)
    return value


# --- Comparison keys ------------------------------------------------------------------


def dimension_of(unit: str, value: Decimal | None = None) -> Dimension:
    if unit in CURRENCIES:
        return ("MONEY", unit)
    if unit in ("PCT", "BPS"):
        return ("RATIO", "")
    if unit in ("COUNT", "PLAIN"):
        return ("NUMBER", "")
    if unit == "DATE":
        return ("DATE", date_precision(value) if value is not None else "D")
    return (unit, "")


def to_key(unit: str, value: Decimal) -> Decimal:
    """``value`` in its dimension's comparison units (basis points become percent)."""
    return value / _HUNDRED if unit == "BPS" else value


def index_key(unit: str, value: Decimal) -> tuple[Dimension, Decimal]:
    return dimension_of(unit, value), to_key(unit, value)
