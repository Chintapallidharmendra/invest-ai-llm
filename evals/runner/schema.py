"""Eval case schema (Story 1.8, AC #1).

One YAML file per case under ``evals/cases/eval/``. Unknown keys are rejected so typos
fail loudly; :func:`load_cases` reports every invalid file with its path.
"""

import glob
from pathlib import Path
from typing import Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Category = Literal[
    "qa",
    "summary",
    "comparison",
    "table_extract",
    "excel_query",
    "excel_op",
    "not_found",
    "multi_turn",
    "concept",
    "scope_decline",
]
SummaryTemplate = Literal["executive", "key_terms", "financial_highlights", "risks", "custom"]
Mode = Literal["api", "model"]
ALL_MODES: tuple[Mode, ...] = ("api", "model")

REPO_ROOT = Path(__file__).resolve().parents[2]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Citation(_Strict):
    """A required citation. Any of ``locator`` or ``also`` satisfies it; a cited sheet
    range satisfies a cell locator inside it."""

    document: str  # corpus file name, e.g. "cim_project_alpha_vardhan_chemicals.pdf"
    locator: str  # "p.12", "slide 4", "Sheet!B2", "para 7" ...
    also: list[str] = Field(default_factory=list)  # equally valid alternative locators


class Expectations(_Strict):
    must_cite: list[Citation] = Field(default_factory=list)
    must_contain_figures: list[str] = Field(default_factory=list)
    must_contain: list[str] = Field(default_factory=list)  # case-insensitive phrases
    must_not_contain: list[str] = Field(default_factory=list)
    expect_decline: str | None = None  # decline category, e.g. "out_of_scope"
    expect_not_found: bool = False
    rubric: str | None = None  # e.g. "summary" -> evals/rubrics/summary.yaml
    reference_output: str | None = None  # path relative to evals/cases/eval/
    reference_ordered: bool = True  # False: compare reference rows as a set


class Case(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    category: Category
    documents: list[str] = Field(default_factory=list)
    turns: list[str] = Field(min_length=1)
    summary_template: SummaryTemplate | None = None
    expectations: Expectations
    modes: list[Mode] = Field(default_factory=lambda: list(ALL_MODES))
    sheet: str | None = None  # excel cases: the sheet the reference output comes from
    notes: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "Case":
        e = self.expectations
        if self.category == "summary" and (self.summary_template is None or e.rubric is None):
            raise ValueError("summary cases need summary_template and expectations.rubric")
        if self.category in {"excel_op", "table_extract"} and not e.reference_output:
            raise ValueError(f"{self.category} cases need expectations.reference_output")
        if self.category == "excel_query" and not (e.reference_output or e.must_contain_figures):
            raise ValueError("excel_query cases need reference_output or must_contain_figures")
        if self.category == "not_found" and not e.expect_not_found:
            raise ValueError("not_found cases need expectations.expect_not_found: true")
        if self.category == "scope_decline" and not e.expect_decline:
            raise ValueError("scope_decline cases need expectations.expect_decline")
        if self.category == "multi_turn" and len(self.turns) < 2:  # noqa: PLR2004
            raise ValueError("multi_turn cases need at least two turns")
        if self.category not in {"concept", "scope_decline"} and not self.documents:
            raise ValueError("cases over documents must list documents")
        cited = {c.document for c in e.must_cite}
        if not cited <= set(self.documents):
            raise ValueError(
                f"must_cite documents not in documents: {sorted(cited - set(self.documents))}"
            )
        return self


class CaseError(ValueError):
    """One or more case files are invalid; the message lists each file and problem."""


def load_case(path: Path) -> Case:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return Case.model_validate(data)
    except (yaml.YAMLError, ValidationError) as exc:
        raise CaseError(f"{path}: {exc}") from exc


def load_cases(pattern: str, base: Path = REPO_ROOT) -> list[Case]:
    """Load every case matching ``pattern`` (relative to the repo root unless absolute)."""
    full = pattern if Path(pattern).is_absolute() else str(base / pattern)
    paths = sorted(Path(p) for p in glob.glob(full, recursive=True))
    if not paths:
        raise CaseError(f"no case files match {pattern!r}")
    cases, errors = [], []
    for path in paths:
        try:
            cases.append(load_case(path))
        except CaseError as exc:
            errors.append(str(exc))
    ids = [c.id for c in cases]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        errors.append(f"duplicate case ids: {duplicates}")
    if errors:
        raise CaseError("\n".join(errors))
    return cases
