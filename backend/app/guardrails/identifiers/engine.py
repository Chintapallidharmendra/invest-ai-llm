"""The identifier engine: Presidio ``AnalyzerEngine`` over an offline spaCy model.

Pipeline for one text:

1. :func:`.recognizers.fold_text` (Unicode digits etc. to ASCII, offsets preserved).
2. Presidio runs every recognizer; :class:`.recognizers.CharWindowContextEnhancer`
   boosts candidates with a context word nearby; candidates under
   ``identifier_min_score`` are dropped.
3. Candidates inside a benign span (:mod:`.allowlist`) are dropped.
4. Candidates on the same span keep the best one (context first, then score, then
   :class:`IdentifierType` order); candidates inside a larger candidate are dropped.

The engine is built once per process (:func:`get_engine`) and is safe to share: every
pattern is compiled during construction and no per-call state is kept. It logs nothing
per call (ADR-033); callers log counts.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

import spacy
from presidio_analyzer import AnalyzerEngine, RecognizerRegistry, RecognizerResult
from presidio_analyzer.nlp_engine import SpacyNlpEngine
from spacy.language import Language

from app.core.obs import get_logger
from app.guardrails.identifiers import allowlist
from app.guardrails.identifiers.recognizers import (
    ENTITY_TYPES,
    CharWindowContextEnhancer,
    build_recognizers,
    fold_text,
)
from app.guardrails.identifiers.settings import IdentifierSettings, get_identifier_settings
from app.guardrails.identifiers.types import Finding, IdentifierType, MaskResult

log = get_logger("guardrails.identifiers")

_LANGUAGE: Final = "en"
_PRIORITY: Final = {t: -i for i, t in enumerate(IdentifierType)}
_CONTEXT_KEY: Final = RecognizerResult.IS_SCORE_ENHANCED_BY_CONTEXT_KEY
# Exercises every pattern once so they are compiled before the first real call.
_WARM_UP_TEXT: Final = "warm-up 0000 a/c x@y IN00 XXXX"


class SpacyModelError(RuntimeError):
    """The configured spaCy model is missing or cannot be loaded."""


def load_spacy_model(path: Path, components: Iterable[str] = ()) -> Language:
    """Load the model at ``path`` keeping only ``components`` (and the tokenizer).

    Never downloads: a missing model is an error (zero egress, ADR-034).
    """
    config_path = path / "config.cfg"
    if not config_path.is_file():
        raise SpacyModelError(
            f"spaCy model not found: {path} (set APP_GUARDRAILS_SPACY_MODEL_PATH to the "
            "directory holding config.cfg)"
        )
    keep = set(components)
    try:
        pipeline = spacy.util.load_config(config_path)["nlp"]["pipeline"]
        missing = keep.difference(pipeline)
        if missing:
            raise SpacyModelError(
                f"spaCy model {path} has no component(s) {', '.join(sorted(missing))}"
            )
        return spacy.load(path, exclude=[name for name in pipeline if name not in keep])
    except SpacyModelError:
        raise
    except Exception as exc:  # spaCy raises OSError, ValueError, KeyError, config errors
        raise SpacyModelError(f"spaCy model unreadable ({type(exc).__name__}): {path}") from None


class _OfflineSpacyNlpEngine(SpacyNlpEngine):
    """Presidio's spaCy engine around an already-loaded model.

    The parent's ``load()`` would download a missing model; this one never loads at all.
    """

    def __init__(self, nlp: Language, model_path: Path) -> None:
        super().__init__(models=[{"lang_code": _LANGUAGE, "model_name": str(model_path)}])
        self.nlp = {_LANGUAGE: nlp}  # type: ignore[assignment]  # Presidio declares it None

    def load(self) -> None:
        return None


@dataclass(slots=True)
class _Candidate:
    type: IdentifierType
    start: int
    end: int
    score: float
    has_context: bool

    @property
    def rank(self) -> tuple[bool, float, int]:
        return (self.has_context, self.score, _PRIORITY[self.type])

    def inside(self, start: int, end: int) -> bool:
        return start <= self.start and self.end <= end


class IdentifierEngine:
    def __init__(self, settings: IdentifierSettings | None = None) -> None:
        settings = settings or get_identifier_settings()
        nlp = load_spacy_model(settings.spacy_model_path, settings.spacy_components)
        self._nlp_engine = _OfflineSpacyNlpEngine(nlp, settings.spacy_model_path)
        recognizers = build_recognizers(settings)
        registry = RecognizerRegistry(recognizers=recognizers, supported_languages=[_LANGUAGE])
        self._min_score = settings.identifier_min_score
        self._analyzer = AnalyzerEngine(
            registry=registry,
            nlp_engine=self._nlp_engine,
            supported_languages=[_LANGUAGE],
            default_score_threshold=self._min_score,
            context_aware_enhancer=CharWindowContextEnhancer(
                recognizers,
                window=settings.identifier_context_window,
                boost=settings.identifier_context_boost,
            ),
        )
        self.detect(_WARM_UP_TEXT)
        log.info(
            "identifiers.ready",
            recognizer_count=len(recognizers),
            spacy_component_count=len(nlp.pipe_names),
        )

    # --- Detection ------------------------------------------------------------------

    def detect(self, text: str) -> list[Finding]:
        """Findings in ``text``, sorted by position. Never holds the matched values."""
        if not text:
            return []
        folded = fold_text(text)
        results = self._analyzer.analyze(
            folded, language=_LANGUAGE, score_threshold=self._min_score
        )
        return self._resolve(folded, results)

    def detect_batch(self, texts: Iterable[str], *, batch_size: int = 64) -> list[list[Finding]]:
        """:meth:`detect` for many texts (one spaCy ``pipe`` over all of them)."""
        folded = [fold_text(t) for t in texts]
        artifacts = self._nlp_engine.process_batch(folded, _LANGUAGE, batch_size=batch_size)
        out: list[list[Finding]] = []
        for text, item in zip(folded, artifacts, strict=True):
            if not text:
                out.append([])
                continue
            results = self._analyzer.analyze(
                text, language=_LANGUAGE, score_threshold=self._min_score, nlp_artifacts=item[1]
            )
            out.append(self._resolve(text, results))
        return out

    def _resolve(self, text: str, results: Sequence[RecognizerResult]) -> list[Finding]:
        candidates = [
            _Candidate(
                ENTITY_TYPES[r.entity_type],
                r.start,
                r.end,
                r.score,
                bool(r.recognition_metadata.get(_CONTEXT_KEY)),
            )
            for r in results
            if r.score >= self._min_score
        ]
        if not candidates:
            return []
        benign = allowlist.benign_spans(text)
        best: dict[tuple[int, int], _Candidate] = {}
        for c in candidates:
            if any(c.inside(start, end) for start, end in benign):
                continue
            current = best.get((c.start, c.end))
            if current is None or c.rank > current.rank:
                best[(c.start, c.end)] = c
        spans = list(best.values())
        kept = [c for c in spans if not any(o is not c and c.inside(o.start, o.end) for o in spans)]
        kept.sort(key=lambda c: (c.start, c.end))
        return [Finding(c.type, c.start, c.end, round(c.score, 4)) for c in kept]

    # --- Masking --------------------------------------------------------------------

    def mask(self, text: str) -> MaskResult:
        """``text`` with each finding replaced by ``[TYPE]``; overlapping findings merge."""
        findings = self.detect(text)
        return MaskResult(apply_mask(text, findings), tuple(findings))

    def mask_batch(self, texts: Iterable[str], *, batch_size: int = 64) -> list[MaskResult]:
        items = list(texts)
        return [
            MaskResult(apply_mask(text, findings), tuple(findings))
            for text, findings in zip(
                items, self.detect_batch(items, batch_size=batch_size), strict=True
            )
        ]


def apply_mask(text: str, findings: Iterable[Finding]) -> str:
    """Replace each finding with its placeholder. Overlapping findings become one
    placeholder, labelled by the highest-scoring (then longest) finding among them."""
    ordered = sorted(findings, key=lambda f: (f.start, f.end))
    parts: list[str] = []
    cursor = 0
    i = 0
    while i < len(ordered):
        group = [ordered[i]]
        end = ordered[i].end
        i += 1
        while i < len(ordered) and ordered[i].start < end:
            group.append(ordered[i])
            end = max(end, ordered[i].end)
            i += 1
        label = max(group, key=lambda f: (f.score, f.end - f.start, _PRIORITY[f.type])).type
        parts.append(text[cursor : group[0].start])
        parts.append(label.placeholder)
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


@lru_cache(maxsize=1)
def get_engine() -> IdentifierEngine:
    """The process-wide engine (built on first use; call at start-up to fail fast)."""
    return IdentifierEngine()


def detect(text: str) -> list[Finding]:
    return get_engine().detect(text)


def detect_batch(texts: Iterable[str]) -> list[list[Finding]]:
    return get_engine().detect_batch(texts)


def mask(text: str) -> MaskResult:
    return get_engine().mask(text)


def mask_batch(texts: Iterable[str]) -> list[MaskResult]:
    return get_engine().mask_batch(texts)
