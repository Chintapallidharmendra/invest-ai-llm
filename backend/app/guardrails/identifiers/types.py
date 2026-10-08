"""Result types of the identifier library. Findings carry positions, never values."""

from dataclasses import dataclass
from enum import StrEnum


class IdentifierType(StrEnum):
    """High-risk identifier types (FR-012). The value is the mask placeholder text."""

    PAN = "PAN"
    AADHAAR = "AADHAAR"
    BANK_ACCOUNT = "BANK_ACCOUNT"
    DEMAT_ID = "DEMAT_ID"
    CARD = "CARD"
    UPI_ID = "UPI_ID"
    PASSPORT = "PASSPORT"
    VOTER_ID = "VOTER_ID"

    @property
    def placeholder(self) -> str:
        return f"[{self.value}]"


@dataclass(frozen=True, slots=True)
class Finding:
    """One identifier at ``text[start:end]`` (code-point offsets into the input).

    Deliberately holds no matched value, so findings are safe to log, count or store.
    """

    type: IdentifierType
    start: int
    end: int
    score: float

    def __post_init__(self) -> None:
        if not 0 <= self.start < self.end:
            raise ValueError("finding needs 0 <= start < end")


@dataclass(frozen=True, slots=True)
class MaskResult:
    """``text`` with every finding replaced by its ``[TYPE]`` placeholder.

    ``findings`` locate the identifiers in the *input* text (for counts and locators).
    """

    text: str
    findings: tuple[Finding, ...]
