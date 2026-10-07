"""Format-valid but fabricated identifiers (FR-012 positives and benign look-alikes).

Check digits are computed properly (Verhoeff for Aadhaar, Luhn for cards and ISINs,
the GSTN mod-36 scheme for GSTIN) so recognizers that validate checksums see them as
real-looking. All values come from a seeded ``random.Random``.
"""

import random
import string
from typing import Final

UPPER: Final = string.ascii_uppercase
ALNUM36: Final = string.digits + string.ascii_uppercase

# Publicly documented payment-network test numbers (never real accounts).
TEST_CARDS: Final = (
    "4111111111111111",  # Visa, 16
    "4012888888881881",  # Visa, 16
    "4222222222222",  # Visa, 13
    "5555555555554444",  # Mastercard, 16
    "5105105105105100",  # Mastercard, 16
    "378282246310005",  # Amex, 15
    "371449635398431",  # Amex, 15
    "6011111111111117",  # Discover, 16
    "3530111333300000",  # JCB, 16
    "30569309025904",  # Diners, 14
)

# --- checksums -------------------------------------------------------------------

_VERHOEFF_D: Final = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P: Final = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)
_VERHOEFF_INV: Final = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def verhoeff_check_digit(digits: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[(i + 1) % 8][int(ch)]]
    return str(_VERHOEFF_INV[c])


def verhoeff_valid(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def luhn_check_digit(digits: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
    return str((10 - total % 10) % 10)


def luhn_valid(number: str) -> bool:
    return luhn_check_digit(number[:-1]) == number[-1]


def gstin_check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        product = ALNUM36.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return ALNUM36[(36 - total % 36) % 36]


def isin_check_digit(first11: str) -> str:
    expanded = "".join(str(ALNUM36.index(ch)) for ch in first11)
    return luhn_check_digit(expanded)


# --- generators ------------------------------------------------------------------


class IdFactory:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng

    def _letters(self, n: int) -> str:
        return "".join(self.rng.choice(UPPER) for _ in range(n))

    def _digits(self, n: int, first_nonzero: bool = False) -> str:
        head = str(self.rng.randint(1, 9)) if first_nonzero else str(self.rng.randint(0, 9))
        return head + "".join(str(self.rng.randint(0, 9)) for _ in range(n - 1))

    # high-risk (FR-012) ---------------------------------------------------------
    def pan(self, holder: str = "P") -> str:
        """Individual PAN: AAA + holder type + surname initial + 4 digits + letter."""
        return self._letters(3) + holder + self._letters(1) + self._digits(4) + self._letters(1)

    def aadhaar(self) -> str:
        body = str(self.rng.randint(2, 9)) + self._digits(10)
        return body + verhoeff_check_digit(body)

    def bank_account(self, length: int) -> str:
        return self._digits(length, first_nonzero=True)

    def demat_nsdl(self) -> str:
        return "IN" + self._digits(14)

    def demat_cdsl(self) -> str:
        return "120" + self._digits(13)

    def card(self, length: int, prefix: str = "60") -> str:
        body = prefix + self._digits(length - len(prefix) - 1)
        return body + luhn_check_digit(body)

    def upi(self, name: str) -> str:
        handle = self.rng.choice(["okhdfcbank", "okicici", "oksbi", "ybl", "paytm", "axl", "ibl"])
        return f"{name}@{handle}"

    def passport(self) -> str:
        return self.rng.choice("ABCGHJKLMNPRSTUVWZ") + self._digits(7, first_nonzero=True)

    def voter_id(self) -> str:
        return self._letters(3) + self._digits(7)

    # benign business identifiers ------------------------------------------------
    def company_pan(self) -> str:
        return self._letters(3) + "C" + self._letters(1) + self._digits(4) + self._letters(1)

    def cin(self, state: str, year: int, listed: bool) -> str:
        industry = self._digits(5, first_nonzero=True)
        kind = "PLC" if listed else "PTC"
        return f"{'L' if listed else 'U'}{industry}{state}{year}{kind}{self._digits(6)}"

    def llpin(self) -> str:
        return f"{self._letters(3)}-{self._digits(4, first_nonzero=True)}"

    def gstin(self, state_code: str, company_pan: str) -> str:
        first14 = f"{state_code}{company_pan}1Z"
        return first14 + gstin_check_char(first14)

    def isin(self) -> str:
        """INE + 4-char issuer + "01" (equity) + 2-digit serial + check digit."""
        issuer = "".join(self.rng.choice(ALNUM36) for _ in range(4))
        first11 = f"INE{issuer}01{self._digits(2)}"
        return first11 + isin_check_digit(first11)


def spaced(number: str, groups: tuple[int, ...], sep: str = " ") -> str:
    out, i = [], 0
    for g in groups:
        out.append(number[i : i + g])
        i += g
    if i < len(number):
        out.append(number[i:])
    return sep.join(out)
