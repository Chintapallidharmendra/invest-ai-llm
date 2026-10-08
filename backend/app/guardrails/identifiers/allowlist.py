"""Benign business identifiers that are never flagged (FR-012).

These are found as spans; the engine drops every candidate that lies entirely inside
one. That is how a company GSTIN suppresses the PAN embedded at its characters 3-12, and
how "Rs 123456789012" stays an amount rather than an Aadhaar number.

Spans are computed on folded text (:func:`.recognizers.fold_text`) and only when a
candidate exists, so the common no-identifier case costs nothing here.
"""

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

_W0: Final = r"(?<![0-9A-Z])"
_W1: Final = r"(?![0-9A-Z])"
_AMOUNT: Final = r"[0-9][0-9,]*(?:\.[0-9]+)?"
_SCALE: Final = r"(?:cr|crores?|lakhs?|lacs?|mn|million|bn|billion|trillion)"
# Letter codes need a word start ("hours 123456789" is not an amount).
_CURRENCY: Final = r"(?:₹|\$|€|£|(?<![A-Z])(?:rs\.?|inr|usd|us\$|eur|gbp))"
_PHONE_DIGITS: Final = r"[0-9](?:[ ()-]{0,2}[0-9]){7,14}"

BENIGN_PATTERNS: Final[Mapping[str, re.Pattern[str]]] = MappingProxyType(
    {
        # 2-digit state code + PAN + entity number + "Z" + check character.
        "gstin": re.compile(
            rf"{_W0}[0-9]{{2}}[A-Z]{{5}}[0-9]{{4}}[A-Z][0-9A-Z]Z[0-9A-Z]{_W1}", re.I
        ),
        # Company Identification Number, e.g. L17110MH1973PLC019786.
        "cin": re.compile(
            rf"{_W0}[LU][0-9]{{5}}[A-Z]{{2}}[0-9]{{4}}[A-Z]{{3}}[0-9]{{6}}{_W1}", re.I
        ),
        # LLP Identification Number, e.g. AAB-1234.
        "llpin": re.compile(rf"{_W0}[A-Z]{{3}}-[0-9]{{4}}{_W1}", re.I),
        # ISIN, e.g. INE002A01018 (country code + 9-character NSIN + check digit).
        "isin": re.compile(rf"{_W0}[A-Z]{{2}}[0-9A-Z]{{9}}[0-9]{_W1}", re.I),
        # Exchange symbols: "NSE: RELIANCE", "BSE:500325", "TCS.NS".
        "symbol": re.compile(
            rf"{_W0}(?:(?:NSE|BSE)\s*[:-]\s*[0-9A-Z&-]{{1,20}}|[0-9A-Z&-]{{1,20}}\.(?:NS|BO)){_W1}",
            re.I,
        ),
        # Numeric dates: 2025-03-31, 31/03/2025, 31.03.25 (with an optional time).
        "date": re.compile(
            rf"{_W0}(?:[0-9]{{4}}[-/.][0-9]{{1,2}}[-/.][0-9]{{1,2}}"
            rf"|[0-9]{{1,2}}[-/.][0-9]{{1,2}}[-/.][0-9]{{2,4}})"
            rf"(?:[T ][0-9]{{1,2}}:[0-9]{{2}}(?::[0-9]{{2}})?)?{_W1}",
            re.I,
        ),
        # Amounts: a currency before, or a scale word after ("₹1,23,45,678", "4,215 cr").
        "money": re.compile(
            rf"(?:{_CURRENCY}\s?{_AMOUNT}(?:\s?{_SCALE}\b)?|{_W0}{_AMOUNT}\s?{_SCALE}\b)", re.I
        ),
        "percent": re.compile(rf"{_W0}{_AMOUNT}\s?%", re.I),
        # Phone numbers in international form, or labelled as a phone number.
        "phone": re.compile(
            rf"(?:\+{_PHONE_DIGITS}"
            rf"|\b(?:tel|telephone|phone|ph|mobile|mob|cell|fax)\b\.?\s*(?:no\.?|number|#)?\s*[:.-]?\s*\+?{_PHONE_DIGITS})"
            rf"(?![0-9])",
            re.I,
        ),
        # Email addresses (a UPI ID has no dotted domain, so it never matches).
        "email": re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+", re.I),
    }
)


def benign_spans(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` of every benign identifier in ``text`` (folded), in no order."""
    return [m.span() for pattern in BENIGN_PATTERNS.values() for m in pattern.finditer(text)]
