"""High-risk personal identifier detection and masking (FR-012; ADR-024, ADR-027).

One deterministic library for the chat gate (4.2), output checks (4.5) and document
masking (9.5)::

    from app.guardrails import identifiers

    identifiers.detect("PAN ABCDE1234F")   # [Finding(type=PAN, start=4, end=14, ...)]
    identifiers.mask("PAN ABCDE1234F")     # MaskResult(text="PAN [PAN]", findings=(...))

Findings carry type, offsets and score, never the matched value. Call
:func:`get_engine` at start-up: it loads the spaCy model from
``APP_GUARDRAILS_SPACY_MODEL_PATH`` and raises :class:`SpacyModelError` if it is missing.
"""

from app.guardrails.identifiers.engine import (
    IdentifierEngine,
    SpacyModelError,
    detect,
    detect_batch,
    get_engine,
    mask,
    mask_batch,
)
from app.guardrails.identifiers.types import Finding, IdentifierType, MaskResult

__all__ = [
    "Finding",
    "IdentifierEngine",
    "IdentifierType",
    "MaskResult",
    "SpacyModelError",
    "detect",
    "detect_batch",
    "get_engine",
    "mask",
    "mask_batch",
]
