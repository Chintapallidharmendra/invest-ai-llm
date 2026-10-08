"""AC #2: masking, overlap merging, and batch == single."""

import dataclasses

import pytest

from app.guardrails import identifiers
from app.guardrails.identifiers.engine import IdentifierEngine, apply_mask
from app.guardrails.identifiers.types import Finding, IdentifierType, MaskResult
from tests.guardrails.identifiers.cases import BENIGN, NEGATIVES, POSITIVES

T = IdentifierType


def test_mask_replaces_each_finding_with_its_placeholder(engine: IdentifierEngine) -> None:
    text = "PAN ABCDE1234F, a/c 123456789 and UPI ravi@okaxis."
    result = engine.mask(text)
    assert result.text == "PAN [PAN], a/c [BANK_ACCOUNT] and UPI [UPI_ID]."
    # Findings locate the identifiers in the input text.
    assert [(f.type, text[f.start : f.end]) for f in result.findings] == [
        (T.PAN, "ABCDE1234F"),
        (T.BANK_ACCOUNT, "123456789"),
        (T.UPI_ID, "ravi@okaxis"),
    ]


@pytest.mark.parametrize("case", POSITIVES, ids=lambda c: f"{c[1].value}:{c[0][:30]!r}")
def test_masked_text_no_longer_holds_the_identifier(
    engine: IdentifierEngine, case: tuple[str, IdentifierType, str]
) -> None:
    text, kind, value = case
    masked = engine.mask(text).text
    assert value not in masked
    assert kind.placeholder in masked
    assert engine.detect(masked) == []


def test_text_without_findings_is_unchanged(engine: IdentifierEngine) -> None:
    for text in ("", "Revenue grew 12% to ₹4,215 cr.", *BENIGN):
        assert engine.mask(text) == MaskResult(text, ())


def test_overlapping_findings_merge_into_one_placeholder() -> None:
    text = "0123456789abcdefghij"
    findings = [
        Finding(T.BANK_ACCOUNT, 2, 10, 0.7),
        Finding(T.CARD, 6, 14, 1.0),  # overlaps the first; higher score labels the merge
        Finding(T.PAN, 14, 16, 0.6),  # adjacent, not overlapping: its own placeholder
        Finding(T.UPI_ID, 18, 20, 0.85),
    ]
    assert apply_mask(text, findings) == "01[CARD][PAN]gh[UPI_ID]"
    assert apply_mask(text, reversed(findings)) == "01[CARD][PAN]gh[UPI_ID]"
    # A finding inside another merges into it.
    assert apply_mask(text, [Finding(T.PAN, 0, 10, 0.6), Finding(T.AADHAAR, 2, 4, 0.6)]) == (
        "[PAN]abcdefghij"
    )


def test_merge_label_ties_go_to_the_longer_finding() -> None:
    findings = [Finding(T.PASSPORT, 0, 4, 0.7), Finding(T.VOTER_ID, 2, 10, 0.7)]
    assert apply_mask("abcdefghijkl", findings) == "[VOTER_ID]kl"


def test_batch_equals_single(engine: IdentifierEngine) -> None:
    texts = [c[0] for c in POSITIVES] + [c[0] for c in NEGATIVES] + BENIGN + ["", " "]
    assert engine.detect_batch(texts) == [engine.detect(t) for t in texts]
    assert engine.mask_batch(texts) == [engine.mask(t) for t in texts]
    assert engine.detect_batch(texts, batch_size=3) == [engine.detect(t) for t in texts]
    assert engine.mask_batch(iter(texts)) == [engine.mask(t) for t in texts]
    assert engine.detect_batch([]) == []


def test_findings_never_hold_the_value(engine: IdentifierEngine) -> None:
    assert [f.name for f in dataclasses.fields(Finding)] == ["type", "start", "end", "score"]
    finding = engine.detect("PAN ABCDE1234F")[0]
    assert "ABCDE1234F" not in repr(finding)
    assert "ABCDE1234F" not in repr(engine.mask("PAN ABCDE1234F"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        finding.start = 0  # type: ignore[misc]
    with pytest.raises(ValueError, match="start < end"):
        Finding(T.PAN, 5, 5, 1.0)


def test_module_level_functions(module_engine: None) -> None:
    text = "PAN ABCDE1234F"
    assert [f.type for f in identifiers.detect(text)] == [T.PAN]
    assert identifiers.mask(text).text == "PAN [PAN]"
    assert identifiers.detect_batch([text, ""]) == [identifiers.detect(text), []]
    assert [r.text for r in identifiers.mask_batch([text])] == ["PAN [PAN]"]
    assert identifiers.get_engine() is identifiers.get_engine()
