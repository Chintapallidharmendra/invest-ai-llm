"""AC #1 and #3: every type in its formats, the negatives, and the benign allow-list."""

from collections import Counter
from pathlib import Path

import pytest

from app.guardrails.identifiers.engine import IdentifierEngine
from app.guardrails.identifiers.recognizers import (
    build_recognizers,
    context_pattern,
    fold_text,
    luhn_valid,
)
from app.guardrails.identifiers.settings import IdentifierSettings
from app.guardrails.identifiers.types import IdentifierType
from tests.guardrails.identifiers.cases import (
    BENIGN,
    NEGATIVES,
    POSITIVES,
    VISA,
    Negative,
    Positive,
    to_fullwidth,
)
from tests.guardrails.identifiers.conftest import spans


def _id(case: Positive | Negative) -> str:
    return f"{case[1].value}:{case[0][:40]!r}"


def test_the_sample_set_has_enough_cases_per_type() -> None:
    positives = Counter(kind for _, kind, _ in POSITIVES)
    negatives = Counter(kind for _, kind in NEGATIVES)
    for kind in IdentifierType:
        assert positives[kind] >= 10, kind
        assert negatives[kind] >= 5, kind


@pytest.mark.parametrize("case", POSITIVES, ids=_id)
def test_positive(engine: IdentifierEngine, case: Positive) -> None:
    text, kind, value = case
    start = text.index(value)
    assert (kind, start, start + len(value)) in spans(engine.detect(text))


@pytest.mark.parametrize("case", NEGATIVES, ids=_id)
def test_negative(engine: IdentifierEngine, case: Negative) -> None:
    text, kind = case
    assert kind not in {f.type for f in engine.detect(text)}


@pytest.mark.parametrize("text", BENIGN, ids=lambda t: repr(t[:40]))
def test_benign_content_is_never_flagged(engine: IdentifierEngine, text: str) -> None:
    assert engine.detect(text) == []


def test_gstin_suppresses_the_embedded_pan(engine: IdentifierEngine) -> None:
    # The PAN at characters 3-12 counts on its own, but not inside the GSTIN.
    text = "GSTIN 27ABCDE1234F1Z5 and PAN ABCDE1234F"
    start = text.rindex("ABCDE1234F")
    assert spans(engine.detect(text)) == [(IdentifierType.PAN, start, start + 10)]


def test_identifiers_next_to_punctuation(engine: IdentifierEngine) -> None:
    for wrapped in ("(ABCDE1234F)", "[ABCDE1234F]", '"ABCDE1234F"', "ABCDE1234F;", "-ABCDE1234F-"):
        found = engine.detect(wrapped)
        assert [(f.type, wrapped[f.start : f.end]) for f in found] == [
            (IdentifierType.PAN, "ABCDE1234F")
        ], wrapped


def test_identifier_at_start_and_end_of_string(engine: IdentifierEngine) -> None:
    text = "ABCDE1234F and ravi@okaxis"
    assert spans(engine.detect(text)) == [
        (IdentifierType.PAN, 0, 10),
        (IdentifierType.UPI_ID, 15, len(text)),
    ]


def test_context_must_be_within_the_window(engine: IdentifierEngine) -> None:
    near = "account " + "x" * 31 + " 123456789"  # word ends 32 chars before; fits in 40
    far = "account " + "x" * 40 + " 123456789"
    assert [f.type for f in engine.detect(near)] == [IdentifierType.BANK_ACCOUNT]
    assert engine.detect(far) == []


def test_specific_context_wins_over_a_checksum_coincidence(engine: IdentifierEngine) -> None:
    # A 16-digit CDSL ID that also happens to pass Luhn is reported as demat, not card.
    cdsl = next(n for n in (f"12012345{i:08d}" for i in range(100)) if luhn_valid(n))
    assert [f.type for f in engine.detect(f"CDSL BO ID {cdsl}")] == [IdentifierType.DEMAT_ID]
    assert [f.type for f in engine.detect(cdsl)] == [IdentifierType.CARD]


def test_a_contained_candidate_is_dropped(engine: IdentifierEngine) -> None:
    # Aadhaar-shaped runs inside a 16-digit card are not reported separately.
    text = " ".join(VISA[i : i + 4] for i in range(0, 16, 4))
    assert [f.type for f in engine.detect(f"aadhaar or card {text}")] == [IdentifierType.CARD]


def test_unicode_digits_are_folded_without_moving_offsets() -> None:
    assert fold_text("२३४५ " + to_fullwidth("6789") + "\u00a0abc") == "2345 6789 abc"
    assert fold_text("plain ascii") == "plain ascii"
    text = "x\u200bABCDE\u20131234F"
    assert len(fold_text(text)) == len(text)


def test_upi_psp_list_is_configurable(spacy_model_path: Path) -> None:
    custom = IdentifierEngine(
        IdentifierSettings(spacy_model_path=spacy_model_path, upi_psp_suffixes=("mybank",))
    )
    assert [f.type for f in custom.detect("pay ravi@mybank")] == [IdentifierType.UPI_ID]
    assert custom.detect("pay ravi@okaxis") == []


def test_settings_reject_bad_psp_suffixes() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        IdentifierSettings(upi_psp_suffixes=(" ",))
    with pytest.raises(ValueError, match="letters, digits"):
        IdentifierSettings(upi_psp_suffixes=("ok.axis",))
    assert IdentifierSettings(upi_psp_suffixes=("YBL ", "ybl")).upi_psp_suffixes == ("ybl",)


def test_presidio_built_ins_are_used() -> None:
    names = {type(r).__name__ for r in build_recognizers(IdentifierSettings())}
    assert {
        "InPanRecognizer",
        "InAadhaarRecognizer",
        "InPassportRecognizer",
        "InVoterRecognizer",
    } <= names
    assert any(
        "CreditCardRecognizer" in {c.__name__ for c in type(r).__mro__}
        for r in build_recognizers(IdentifierSettings())
    )


def test_context_pattern_matches_word_starts_only() -> None:
    pattern = context_pattern(["a/c", "dp id", "bank"])
    assert pattern.search("A/C no")
    assert pattern.search("DP  ID:")
    assert pattern.search("banking")  # a word start: "bank" covers "banking"
    assert not pattern.search("databank")
