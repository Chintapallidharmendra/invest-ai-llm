"""Presidio recognizers for the high-risk identifiers (FR-012, ADR-024).

Presidio's built-ins (``IN_PAN``, ``IN_AADHAAR``, ``IN_PASSPORT``, ``IN_VOTER``,
``CREDIT_CARD``) run alongside custom recognizers that cover the forms they miss:
spaced PAN, masked Aadhaar, bank account, demat, any-issuer cards, UPI, and passport
and voter IDs without the built-ins' digit restrictions.

Scoring (decided in :mod:`.engine` against ``identifier_min_score``, default 0.5):

- Distinctive shapes score at or above the threshold on their own.
- Checksummed shapes (Aadhaar/Verhoeff, cards/Luhn) score 1.0 when valid and are
  discarded when not.
- Context-gated shapes (bank account, CDSL demat, masked Aadhaar, spaced PAN, passport,
  voter ID) score below it and pass only when :class:`CharWindowContextEnhancer` finds a
  context word of their type within ``identifier_context_window`` characters.

Every pattern runs on text already passed through :func:`fold_text`, so ``[0-9]`` and
``[A-Z]`` also cover Unicode digits and fullwidth letters. Patterns are case-insensitive
(Presidio's default flags).
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache
from types import MappingProxyType
from typing import Final

from presidio_analyzer import EntityRecognizer, Pattern, PatternRecognizer, RecognizerResult
from presidio_analyzer.context_aware_enhancers import ContextAwareEnhancer
from presidio_analyzer.nlp_engine import NlpArtifacts
from presidio_analyzer.predefined_recognizers import (
    CreditCardRecognizer,
    InAadhaarRecognizer,
    InPanRecognizer,
    InPassportRecognizer,
    InVoterRecognizer,
)

from app.guardrails.identifiers.settings import IdentifierSettings
from app.guardrails.identifiers.types import IdentifierType

# --- Text folding ------------------------------------------------------------------

_DASHES: Final = frozenset(chr(c) for c in (*range(0x2010, 0x2016), 0x2212, 0xFE58, 0xFE63))
_FULLWIDTH: Final = range(0xFF01, 0xFF5F)  # fullwidth "!" to "~"
_FULLWIDTH_OFFSET: Final = 0xFEE0
_NON_ASCII: Final = re.compile(r"[^\x00-\x7f]")


@lru_cache(maxsize=4096)
def _fold_char(char: str) -> str:
    digit = unicodedata.decimal(char, None)
    if digit is not None:
        return str(digit)
    if ord(char) in _FULLWIDTH:
        return chr(ord(char) - _FULLWIDTH_OFFSET)
    if char in _DASHES:
        return "-"
    if unicodedata.category(char) in {"Zs", "Cf"}:  # odd spaces; zero-width characters
        return " "
    return char


def fold_text(text: str) -> str:
    """Map Unicode digits, fullwidth ASCII, dashes and odd spaces to ASCII.

    One code point always maps to one code point, so offsets into the folded text are
    offsets into the original.
    """
    if text.isascii():
        return text
    return _NON_ASCII.sub(lambda m: _fold_char(m.group()), text)


# --- Validators -------------------------------------------------------------------


def digits_of(text: str) -> str:
    return "".join(c for c in text if "0" <= c <= "9")


def luhn_valid(digits: str) -> bool:
    """Luhn checksum over an all-digit string."""
    total = 0
    for i, char in enumerate(reversed(digits)):
        value = ord(char) - 48
        if i % 2 == 1:
            value = value * 2 - 9 if value > 4 else value * 2  # noqa: PLR2004
        total += value
    return total % 10 == 0


# --- Context words ----------------------------------------------------------------

# Matched case-insensitively at a word start (so "account" also covers "accounts").
CONTEXT_WORDS: Final[Mapping[IdentifierType, tuple[str, ...]]] = MappingProxyType(
    {
        IdentifierType.PAN: ("pan", "permanent account number", "income tax"),
        IdentifierType.AADHAAR: ("aadhaar", "aadhar", "adhaar", "uidai", "uid", "आधार"),
        IdentifierType.BANK_ACCOUNT: (
            "account",
            "a/c",
            "acct",
            "acc no",
            "acc. no",
            "ac no",
            "ac. no",
            "ifsc",
            "bank",
            "savings",
            "beneficiary",
        ),
        IdentifierType.DEMAT_ID: (
            "demat",
            "dp id",
            "dp-id",
            "dpid",
            "bo id",
            "boid",
            "beneficiary owner",
            "client id",
            "cdsl",
            "nsdl",
            "depository",
        ),
        IdentifierType.CARD: (
            "card",
            "credit",
            "debit",
            "visa",
            "mastercard",
            "rupay",
            "amex",
            "maestro",
            "cvv",
        ),
        IdentifierType.UPI_ID: ("upi", "vpa", "gpay", "phonepe", "paytm", "bhim"),
        IdentifierType.PASSPORT: ("passport", "travel document"),
        IdentifierType.VOTER_ID: (
            "voter",
            "epic",
            "elector",
            "election card",
            "election id",
            "electoral",
        ),
    }
)


def context_pattern(words: Iterable[str]) -> re.Pattern[str]:
    alternatives = sorted({w.strip().lower() for w in words if w.strip()}, key=len, reverse=True)
    body = "|".join(r"\s+".join(re.escape(part) for part in w.split()) for w in alternatives)
    return re.compile(rf"(?<![a-z0-9])(?:{body})", re.IGNORECASE)


# --- Patterns -----------------------------------------------------------------------

# Boundaries. Letter-led shapes use \b (several times faster in the regex module than
# a lookbehind at every position). Digit runs may not touch a letter or digit, nor
# continue as a decimal or thousands group ("123456789.50", "1,234567890").
_D0: Final = r"(?<![0-9A-Z])(?<![0-9][.,])"
_D1: Final = r"(?![0-9A-Z])(?![.,][0-9])"

# Base scores; see the module docstring.
STRONG: Final = 0.85
SHAPE: Final = 0.6
GATED: Final = 0.3

_PAN_PATTERNS: Final = (
    Pattern("pan", r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", SHAPE),
    # Separated forms ("ABCDE 1234 F") also match ordinary phrases ("TABLE 2023 A").
    Pattern("pan_separated", r"\b[A-Z]{5}(?:[ -][0-9]{4}[ -]?|[0-9]{4}[ -])[A-Z]\b", GATED),
)
# Last four digits shown; the first eight masked with X, * or bullets. Not the tail of
# a masked card ("XXXX XXXX XXXX 1234").
_AADHAAR_MASKED_PATTERNS: Final = (
    Pattern(
        "aadhaar_masked",
        r"(?<![0-9A-Z*•])(?<![0-9X*•][ -])"
        rf"[X*•]{{4}}([ -]?)[X*•]{{4}}\1[0-9]{{4}}{_D1}",
        GATED,
    ),
)
_BANK_ACCOUNT_PATTERNS: Final = (Pattern("bank_account", rf"{_D0}[0-9]{{9,18}}{_D1}", GATED),)
_DEMAT_PATTERNS: Final = (
    # NSDL: "IN" + 6-digit DP ID + 8-digit client ID.
    Pattern("demat_nsdl", rf"\bIN[0-9]{{6}}[ -]?[0-9]{{8}}{_D1}", STRONG),
    # CDSL: 16 digits (8-digit DP ID + 8-digit client ID).
    Pattern("demat_cdsl", rf"{_D0}[0-9]{{8}}[ -]?[0-9]{{8}}{_D1}", GATED),
)
# Any issuer; Luhn decides. Grouped forms keep one separator throughout.
_CARD_PATTERNS: Final = (
    Pattern("card", rf"{_D0}[0-9]{{13,19}}{_D1}", GATED),
    Pattern("card_4s", rf"{_D0}[0-9]{{4}}([ -])[0-9]{{4}}\1[0-9]{{4}}\1[0-9]{{1,7}}{_D1}", GATED),
    Pattern(
        "card_19",
        rf"{_D0}[0-9]{{4}}([ -])[0-9]{{4}}\1[0-9]{{4}}\1[0-9]{{4}}\1[0-9]{{3}}{_D1}",
        GATED,
    ),
    Pattern("card_4_6_x", rf"{_D0}[0-9]{{4}}([ -])[0-9]{{6}}\1[0-9]{{4,5}}{_D1}", GATED),
)
# One letter + 7 digits (the built-in also requires 1-9 in two places).
_PASSPORT_PATTERNS: Final = (Pattern("passport", r"\b[A-Z] ?[0-9]{7}\b", GATED),)
# EPIC: 3 letters + 7 digits, optionally separated.
_VOTER_PATTERNS: Final = (Pattern("voter", r"\b[A-Z]{3}[ /-]?[0-9]{7}\b", GATED),)


def _upi_patterns(suffixes: Sequence[str]) -> list[Pattern]:
    handles = "|".join(re.escape(s) for s in sorted(suffixes, key=len, reverse=True))
    # Not followed by ".tld", which would make it an email address.
    regex = (
        rf"(?<![A-Z0-9._-])[A-Z0-9][A-Z0-9._-]{{1,99}}@(?:{handles})"
        r"(?![A-Z0-9_-])(?!\.[A-Z0-9])"
    )
    return [Pattern("upi", regex, STRONG)]


class _CardRecognizer(CreditCardRecognizer):
    """Presidio's Luhn check over any-issuer patterns; one repeated digit is rejected."""

    def invalidate_result(self, pattern_text: str) -> bool:
        return len(set(digits_of(pattern_text))) < 2  # noqa: PLR2004


def _custom(entity: IdentifierType, patterns: Iterable[Pattern]) -> PatternRecognizer:
    return PatternRecognizer(
        supported_entity=entity.value,
        name=f"{entity.value.lower()}_recognizer",
        patterns=list(patterns),
        context=list(CONTEXT_WORDS[entity]),
    )


# Presidio entity name -> identifier type (custom recognizers use the type's own name).
ENTITY_TYPES: Final[Mapping[str, IdentifierType]] = MappingProxyType(
    {
        "IN_PAN": IdentifierType.PAN,
        "IN_AADHAAR": IdentifierType.AADHAAR,
        "IN_PASSPORT": IdentifierType.PASSPORT,
        "IN_VOTER": IdentifierType.VOTER_ID,
        "CREDIT_CARD": IdentifierType.CARD,
        **{t.value: t for t in IdentifierType},
    }
)


def build_recognizers(settings: IdentifierSettings) -> list[EntityRecognizer]:
    """Presidio's India and card recognizers (patterns widened where the spec needs more
    forms; Aadhaar keeps Presidio's own patterns and Verhoeff check) plus the custom ones.

    IN_PAN's built-in "Low" pattern (any 10-character word with 4 digits) is not used: it
    can never pass the threshold and rescans the rest of the text at every word.
    """
    ctx = {t: list(words) for t, words in CONTEXT_WORDS.items()}
    return [
        InPanRecognizer(patterns=list(_PAN_PATTERNS), context=ctx[IdentifierType.PAN]),
        InAadhaarRecognizer(context=ctx[IdentifierType.AADHAAR]),
        InPassportRecognizer(
            patterns=list(_PASSPORT_PATTERNS), context=ctx[IdentifierType.PASSPORT]
        ),
        InVoterRecognizer(patterns=list(_VOTER_PATTERNS), context=ctx[IdentifierType.VOTER_ID]),
        _CardRecognizer(patterns=list(_CARD_PATTERNS), context=ctx[IdentifierType.CARD]),
        _custom(IdentifierType.AADHAAR, _AADHAAR_MASKED_PATTERNS),
        _custom(IdentifierType.BANK_ACCOUNT, _BANK_ACCOUNT_PATTERNS),
        _custom(IdentifierType.DEMAT_ID, _DEMAT_PATTERNS),
        _custom(IdentifierType.UPI_ID, _upi_patterns(settings.upi_psp_suffixes)),
    ]


# --- Context ------------------------------------------------------------------------


class CharWindowContextEnhancer(ContextAwareEnhancer):
    """Boosts a result when its recognizer's context words lie within ``window``
    characters before its start or after its end.

    Replaces Presidio's lemma enhancer: matching on characters needs no tagger or
    lemmatizer (see ``IdentifierSettings.spacy_components``) and is deterministic.
    """

    def __init__(self, recognizers: Iterable[EntityRecognizer], window: int, boost: float):
        super().__init__(
            context_similarity_factor=boost,
            min_score_with_context_similarity=0,
            context_prefix_count=0,
            context_suffix_count=0,
        )
        self.window = window
        self._patterns: dict[str, re.Pattern[str]] = {
            r.id: context_pattern(r.context)
            for r in recognizers
            if isinstance(r, PatternRecognizer) and r.context
        }

    def enhance_using_context(
        self,
        text: str,
        raw_results: list[RecognizerResult],
        nlp_artifacts: NlpArtifacts,
        recognizers: list[EntityRecognizer],
        context: list[str] | None = None,
    ) -> list[RecognizerResult]:
        for result in raw_results:
            recognizer_id = result.recognition_metadata.get(
                RecognizerResult.RECOGNIZER_IDENTIFIER_KEY
            )
            pattern = self._patterns.get(recognizer_id) if isinstance(recognizer_id, str) else None
            if pattern is None:
                continue
            before = pattern.search(text, max(0, result.start - self.window), result.start)
            if (
                before is None
                and pattern.search(text, result.end, result.end + self.window) is None
            ):
                continue
            result.score = min(self.MAX_SCORE, result.score + self.context_similarity_factor)
            result.recognition_metadata[RecognizerResult.IS_SCORE_ENHANCED_BY_CONTEXT_KEY] = True
        return raw_results
