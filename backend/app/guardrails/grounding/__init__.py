"""Figure grounding (FR-036, ADR-025): extract the numbers in a text, normalise them,
and check each against the figures of the turn's sources.

Pure and deterministic: no I/O, logging or settings. Consumers: the output gate (4.5),
summary bullets (9.10) and comparison tables (9.12)::

    from app.guardrails import grounding as g

    sources = g.build_grounding_set([
        g.SourceText("Revenue was ₹4,215.37 crore in FY25.", origin="doc1#p.12"),
        g.TableCell("12.5", origin="doc1#t3", unit=g.Unit.PCT),
    ])
    [r.reason for r in g.ground_text("Revenue: ₹42,154 million (FY25).", sources)]
    # [MatchReason.MATCHED, MatchReason.EXEMPT]
"""

from app.guardrails.grounding.extract import extract_figures
from app.guardrails.grounding.match import build_grounding_set, ground_text, is_exempt, match
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

__all__ = [
    "ExplicitValue",
    "Figure",
    "FigureKind",
    "GroundedValue",
    "GroundingSet",
    "MatchReason",
    "MatchResult",
    "Source",
    "SourceText",
    "TableCell",
    "Unit",
    "build_grounding_set",
    "extract_figures",
    "ground_text",
    "is_exempt",
    "match",
]
