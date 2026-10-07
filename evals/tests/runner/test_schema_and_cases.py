"""AC #1-#2: schema validation and the authored eval set."""

import filecmp
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from evals.runner import corpus
from evals.runner.authoring import author
from evals.runner.cli import MINIMUMS, count, main
from evals.runner.schema import REPO_ROOT, CaseError, load_cases

CASES = "evals/cases/eval/*.yaml"
VALID = {
    "id": "qa-example",
    "category": "qa",
    "documents": ["cim_project_alpha_vardhan_chemicals.pdf"],
    "turns": ["What was FY2024 revenue?"],
    "expectations": {
        "must_cite": [{"document": "cim_project_alpha_vardhan_chemicals.pdf", "locator": "p.3"}],
        "must_contain_figures": ["1,579.4"],
    },
}


def _write(tmp_path: Path, name: str, data: dict) -> str:
    (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(tmp_path / "*.yaml")


def test_valid_case_loads(tmp_path: Path) -> None:
    (case,) = load_cases(_write(tmp_path, "ok", VALID))
    assert case.expectations.must_cite[0].locator == "p.3"
    assert case.modes == ["api", "model"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"category": "trivia"}, "category"),
        ({"surprise": 1}, "surprise"),
        ({"turns": []}, "turns"),
        ({"id": "Bad Id"}, "id"),
        ({"category": "summary"}, "summary_template"),
        ({"category": "excel_op"}, "reference_output"),
        ({"category": "not_found"}, "expect_not_found"),
        ({"category": "multi_turn"}, "two turns"),
        ({"category": "scope_decline"}, "expect_decline"),
        ({"documents": ["term_sheet_v3.docx"]}, "must_cite documents not in documents"),
        ({"expectations": {"must_cite": [{"document": "x.pdf"}]}}, "locator"),
    ],
)
def test_invalid_cases_rejected_with_clear_errors(
    tmp_path: Path, change: dict, message: str
) -> None:
    pattern = _write(tmp_path, "bad", {**VALID, **change})
    with pytest.raises(CaseError) as excinfo:
        load_cases(pattern)
    assert "bad.yaml" in str(excinfo.value)
    assert message in str(excinfo.value)


def test_duplicate_ids_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "a", VALID)
    with pytest.raises(CaseError, match="duplicate case ids"):
        load_cases(_write(tmp_path, "b", VALID))


def test_eval_set_meets_minimums(capsys: pytest.CaptureFixture[str]) -> None:
    cases = load_cases(CASES)
    counts, problems = count(cases)
    assert problems == []
    assert len(cases) >= 80
    assert all(counts[c] >= n for c, n in MINIMUMS.items())
    assert main(["count"]) == 0
    assert "total" in capsys.readouterr().out


def test_committed_cases_match_a_fresh_authoring_run(tmp_path: Path) -> None:
    author(tmp_path / "eval")
    committed = REPO_ROOT / "evals" / "cases" / "eval"
    fresh = sorted(
        p.relative_to(tmp_path / "eval") for p in (tmp_path / "eval").rglob("*") if p.is_file()
    )
    assert fresh == sorted(p.relative_to(committed) for p in committed.rglob("*") if p.is_file())
    mismatched = [
        p for p in fresh if not filecmp.cmp(committed / p, tmp_path / "eval" / p, shallow=False)
    ]
    assert mismatched == [], "cases are stale: run `python -m evals.runner author`"


def test_case_facts_come_from_the_corpus() -> None:
    """Every expected figure and citation exists in the corpus documents."""
    for case in load_cases(CASES):
        text = {doc: " ".join(corpus.full_text(doc).split()) for doc in case.documents}
        for fig in case.expectations.must_contain_figures:
            plain = fig.replace("₹", "")
            assert (
                any(plain in t or fig in t for t in text.values()) or case.category == "excel_query"
            ), (case.id, fig)
        for cite in case.expectations.must_cite:
            locators = {u.locator for u in corpus.units(cite.document)}
            if "!" in cite.locator:
                assert cite.locator.split("!")[0] in {loc.split("!")[0] for loc in locators}, (
                    case.id,
                    cite,
                )
            else:
                assert cite.locator in locators, (case.id, cite.locator)
