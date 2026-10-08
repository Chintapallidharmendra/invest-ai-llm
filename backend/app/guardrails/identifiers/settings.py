"""Identifier-recognizer settings (ADR-024, ADR-027, ADR-034).

Environment (``APP_GUARDRAILS_`` prefix), e.g. ``APP_GUARDRAILS_SPACY_MODEL_PATH``.
Lists are JSON, e.g. ``APP_GUARDRAILS_UPI_PSP_SUFFIXES='["ybl", "okaxis"]'``.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Final

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

from app.core.config import settings_config

# The model directory (the one holding config.cfg), staged read-only under /models.
DEFAULT_SPACY_MODEL_PATH: Final = Path("/models/spacy/en_core_web_sm")

# UPI handles (the part after "@") of common PSP apps and banks. Only these count as
# UPI IDs; anything else after "@" is left alone (business emails have a dotted domain).
DEFAULT_UPI_PSP_SUFFIXES: Final = (
    "abfspay",
    "airtel",
    "apl",
    "aubank",
    "axisb",
    "axisbank",
    "axl",
    "barodampay",
    "boi",
    "cbin",
    "citi",
    "cnrb",
    "dbs",
    "federal",
    "fbl",
    "freecharge",
    "hdfcbank",
    "hsbc",
    "ibl",
    "icici",
    "idbi",
    "idfcbank",
    "ikwik",
    "indus",
    "jio",
    "jupiteraxis",
    "kbl",
    "kmbl",
    "kotak",
    "kvb",
    "mahb",
    "naviaxis",
    "okaxis",
    "okhdfcbank",
    "okicici",
    "oksbi",
    "paytm",
    "pingpay",
    "pnb",
    "postbank",
    "ptaxis",
    "pthdfc",
    "ptsbi",
    "ptyes",
    "rapl",
    "rbl",
    "sbi",
    "sc",
    "slice",
    "superyes",
    "uboi",
    "unionbank",
    "upi",
    "waaxis",
    "wahdfcbank",
    "waicici",
    "wasbi",
    "yapl",
    "ybl",
    "yesbank",
    "yesbankltd",
)

Score = Annotated[float, Field(gt=0, le=1)]


class IdentifierSettings(BaseSettings):
    model_config = settings_config("APP_GUARDRAILS_")

    # spaCy model directory. The engine fails at start-up if it is missing; nothing is
    # ever downloaded (zero egress).
    spacy_model_path: Path = DEFAULT_SPACY_MODEL_PATH
    # spaCy components to keep. Empty = tokenizer only: every decision is made by
    # regexes, validators and character-window context, and the tagger/lemmatizer alone
    # would cost ~20 ms per 2,000 characters (the whole p95 budget).
    spacy_components: tuple[str, ...] = ()

    # A candidate is reported when its score reaches this. Context-gated patterns score
    # below it on their own and reach it only with a context word nearby.
    identifier_min_score: Score = 0.5
    # Added to a candidate's score when a context word of its type lies within the window
    # (characters before the start or after the end).
    identifier_context_boost: Score = 0.4
    identifier_context_window: Annotated[int, Field(ge=1, le=200)] = 40

    upi_psp_suffixes: tuple[str, ...] = DEFAULT_UPI_PSP_SUFFIXES

    @field_validator("upi_psp_suffixes")
    @classmethod
    def _check_suffixes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(sorted({s.strip().lower() for s in value if s.strip()}))
        if not cleaned:
            raise ValueError("upi_psp_suffixes must not be empty")
        bad = [s for s in cleaned if not s.isascii() or not s.replace("-", "").isalnum()]
        if bad:
            raise ValueError("upi_psp_suffixes may only hold letters, digits and '-'")
        return cleaned


@lru_cache
def get_identifier_settings() -> IdentifierSettings:
    return IdentifierSettings()
