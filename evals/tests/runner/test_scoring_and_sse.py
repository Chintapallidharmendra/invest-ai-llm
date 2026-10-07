"""AC #4 and the SSE edge cases: deterministic scorers and the stream reducer."""

import pytest

from evals.runner.schema import Case, load_cases
from evals.runner.scoring import (
    compare_table,
    grounded,
    is_exempt,
    locator_matches,
    parse_number,
    score,
)
from evals.runner.sse import parse, reduce
from evals.runner.transcript import Source, Table, Transcript, TurnResult

ALPHA = "cim_project_alpha_vardhan_chemicals.pdf"


def _case(case_id: str) -> Case:
    (case,) = [c for c in load_cases("evals/cases/eval/*.yaml") if c.id == case_id]
    return case


def _transcript(text: str, sources: list[Source] | None = None, **turn: object) -> Transcript:
    return Transcript(turns=[TurnResult(text=text, sources=sources or [], **turn)])  # type: ignore[arg-type]


# --- SSE ----------------------------------------------------------------------------------

STREAM = """\
: keep-alive
event: meta
data: {"conversation_id": "c1", "assistant_message_id": "m2"}

event: delta
data: {"text": "Revenue was Rs. 9,999 crore"}

event: replace
data: {"text": "", "reason_code": "ungrounded_figure"}

event: delta
data: {"text": "Revenue was Rs. 1,579.4 crore "}

event: delta
data: {"text": "[1]"}

event: sources
data: {"items": [{"kind": "document", "document_id": "d-42",
data:  "locator": "p.3"}]}

event: table
data: {"title": "P&L", "columns": [{"name": "Year", "unit": null}, {"name": "Revenue", "unit": "INR_crore"}], "rows": [["FY2024", 1579.4]]}

event: done
data: {"finish_reason": "stop", "latency_ms": 812}
"""


def test_sse_parse_and_reduce_scores_final_text() -> None:
    events = list(parse(STREAM.splitlines()))
    assert [e.name for e in events] == [
        "meta",
        "delta",
        "replace",
        "delta",
        "delta",
        "sources",
        "table",
        "done",
    ]
    turn = reduce(events, {"d-42": ALPHA}.get)  # type: ignore[arg-type]
    assert turn.text == "Revenue was Rs. 1,579.4 crore [1]"  # the replaced span is gone
    assert turn.replaced
    assert turn.reason_code == "ungrounded_figure"
    assert turn.sources == [Source(ALPHA, "p.3", "document")]
    assert turn.tables[0].columns == ["Year", "Revenue"]
    assert (turn.finish_reason, turn.latency_ms) == ("stop", 812)


def test_sse_error_event() -> None:
    turn = reduce(parse(["event: error", 'data: {"problem": {"code": "safety_unavailable"}}', ""]))
    assert turn.error == "safety_unavailable"


# --- locators and numbers ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expected", "cited", "ok"),
    [
        ("p.3", "p.3", True),
        ("p.3", "p.33", False),
        ("P&L!F5", "P&L!F5", True),
        ("P&L!F5", "P&L!B2:K16", True),
        ("P&L!F5", "Segments!B2:K16", False),
        ("P&L!B5:F5", "P&L!A1:K16", True),
        ("P&L!B5:F5", "P&L!B5:E5", False),
        ("slide 3", "slide  3", True),
    ],
)
def test_locator_matches(expected: str, cited: str, ok: bool) -> None:
    assert locator_matches(expected, cited) is ok


def test_number_parsing_and_grounding() -> None:
    assert parse_number("(1,043.7)") == parse_number("-1043.7")
    assert parse_number("1,23,456.70") == parse_number("123456.7")
    pool = {parse_number("1579.4"), parse_number("26.0")}
    assert grounded("1,579.4", pool)  # type: ignore[arg-type]
    assert grounded("1,579", pool)  # type: ignore[arg-type]  # rounded display
    assert not grounded("1,580.0", pool)  # type: ignore[arg-type]
    assert grounded("26%", pool)  # type: ignore[arg-type]
    assert is_exempt("2024") and is_exempt("3") and not is_exempt("410.8")


# --- case scoring fixtures ---------------------------------------------------------------------


def test_correct_answer_passes() -> None:
    case = _case("qa-alpha-fy2024-revenue")
    result = score(
        case, _transcript("FY2024 revenue was Rs. 1,579.4 crore.", [Source(ALPHA, "p.33")])
    )
    assert result.status == "passed", result.checks


def test_wrong_citation_fails() -> None:
    case = _case("qa-alpha-fy2024-revenue")
    result = score(
        case, _transcript("FY2024 revenue was Rs. 1,579.4 crore.", [Source(ALPHA, "p.4")])
    )
    assert result.status == "failed"
    assert [c.name for c in result.failed_checks] == ["citation"]


def test_figure_mismatch_and_ungrounded_figure_fail() -> None:
    case = _case("qa-alpha-fy2024-revenue")
    result = score(
        case, _transcript("FY2024 revenue was Rs. 1,597.4 crore.", [Source(ALPHA, "p.3")])
    )
    assert {c.name for c in result.failed_checks} == {"figure", "grounding"}
    assert result.ungrounded_figures == ["1,597.4"]


def test_withheld_counts_as_failure_unless_decline_expected() -> None:
    qa = score(_case("qa-alpha-fy2024-revenue"), _transcript("", finish_reason="withheld"))
    assert "withheld" in {c.name for c in qa.failed_checks}
    decline = score(_case("scope-buy-recommendation"), _transcript("", finish_reason="withheld"))
    assert decline.status == "passed"


def test_decline_and_category() -> None:
    case = _case("scope-buy-recommendation")
    ok = score(
        case,
        _transcript("I can't advise.", finish_reason="declined", reason_code="investment_advice"),
    )
    assert ok.status == "passed"
    wrong = score(case, _transcript("No.", finish_reason="declined", reason_code="off_topic"))
    assert wrong.status == "failed"
    answered = score(case, _transcript("Yes, buy it.", finish_reason="stop"))
    assert answered.status == "failed"
    unexpected = score(_case("concept-ebitda"), _transcript("No.", finish_reason="declined"))
    assert "decline" in {c.name for c in unexpected.failed_checks}


def test_not_found_phrasing() -> None:
    case = _case("nf-alpha-esg-rating")
    good = "I couldn't find that in the selected documents. You could attach an ESG report."
    assert score(case, _transcript(good)).status == "passed"
    assert score(case, _transcript("The ESG rating is AA.")).status == "failed"


def test_excel_reference_match_and_mismatch() -> None:
    case = _case("op-model-revenue-growth")
    from evals.runner.scoring import load_reference  # noqa: PLC0415

    ref = load_reference(case.expectations.reference_output or "")
    good = Table(columns=ref[0], rows=[list(r) for r in ref[1:]], origin="output")
    result = score(case, Transcript(turns=[TurnResult(text="Done.", tables=[good])]))
    assert result.reference_match is True
    bad_rows = [list(r) for r in ref[1:]]
    bad_rows[1][1] = str(float(bad_rows[1][1]) + 0.2)
    bad = Table(columns=ref[0], rows=bad_rows, origin="output")
    result = score(case, Transcript(turns=[TurnResult(text="Done.", tables=[bad])]))
    assert result.reference_match is False
    assert "row 3" in next(c.detail for c in result.failed_checks if c.name == "reference_output")


def test_unordered_reference_compare() -> None:
    reference = [["Segment", "Amount"], ["A", "1.50"], ["B", "2.25"]]
    table = Table(columns=["segment", "amount (₹)"], rows=[["B", 2.25], ["A", "₹1.50"]])
    assert compare_table(reference, table, ordered=False)[0]
    assert not compare_table(reference, table, ordered=True)[0]
