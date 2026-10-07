"""Deterministic scoring (Story 1.8, AC #4).

Checks run on the **final** turn of a case: its text after any ``replace``, its
sources and its tables.

- **Citations:** every ``must_cite`` entry needs a source with the same document and a
  matching locator. A cited sheet range covers the cells inside it, and ``also`` lists
  equally valid locators.
- **Figures:** exact, through the grounding library (Story 4.4) when it is importable;
  otherwise an exact string match after whitespace and currency-marker normalisation.
- **Ungrounded figures:** numbers in the answer that are not in the grounding set (the
  case's documents, the user's turns, engine tables and reference outputs), using the
  display-precision rule of ADR-025. Years, small counts and citation locators are exempt.
- **Decline and not found:** ``done.finish_reason == "declined"`` (plus the category, when
  the target reports one), and FR-030's "couldn't find that in the selected documents"
  phrasing. ``withheld`` fails unless a decline was expected.
- **Reference outputs:** cell-by-cell. Numbers are compared at the reference's printed
  precision; strings are case- and whitespace-insensitive.
"""

import csv
import importlib
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext
from functools import cache
from typing import Any, Final

from openpyxl.utils.cell import range_boundaries  # type: ignore[import-untyped]

from evals.runner import corpus
from evals.runner.schema import REPO_ROOT, Case, Citation
from evals.runner.transcript import Table, Transcript

CASES_DIR: Final = REPO_ROOT / "evals" / "cases" / "eval"
NOT_FOUND_RE: Final = re.compile(
    r"(couldn['\u2019]t|could not|cannot|can['\u2019]t) find (that|this|it)? ?"
    r"in the selected documents",
    re.I,
)
NUMBER_RE: Final = re.compile(r"(?<![\w.])\(?-?\d[\d,]*(?:\.\d+)?\)?%?(?![\w])")
CITATION_TEXT_RE: Final = re.compile(r"\[[^\]]*\]|cite:\S+")
YEAR_RANGE: Final = range(1900, 2101)
SMALL_COUNT: Final = 12


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class CaseResult:
    case_id: str
    category: str
    status: str  # passed | failed | skipped | error | needs_review
    checks: list[Check] = field(default_factory=list)
    skipped_reason: str | None = None
    error: str | None = None
    summary_score: float | None = None
    judge: dict[str, Any] | None = None
    override: dict[str, Any] | None = None
    ungrounded_figures: list[str] = field(default_factory=list)
    reference_match: bool | None = None
    latency_ms: int | None = None

    @property
    def failed_checks(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


# --- optional app libraries ----------------------------------------------------------------


@cache
def grounding_lib() -> Any | None:
    """``app.guardrails.grounding`` (Story 4.4) if it is available."""
    backend = str(REPO_ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    try:
        return importlib.import_module("app.guardrails.grounding")
    except ImportError:
        return None


def grounding_method() -> str:
    return (
        "app.guardrails.grounding"
        if grounding_lib()
        else "exact string / numeric precision fallback"
    )


# --- locators ----------------------------------------------------------------------------------


def _split_sheet(locator: str) -> tuple[str, str] | None:
    if "!" not in locator:
        return None
    sheet, _, ref = locator.rpartition("!")
    return sheet.strip("'"), ref.replace("$", "")


def locator_matches(expected: str, cited: str) -> bool:
    """True if ``cited`` points at ``expected``: equal (whitespace-insensitive), or a
    sheet range containing the expected cell or range."""
    if " ".join(expected.split()).lower() == " ".join(cited.split()).lower():
        return True
    e, c = _split_sheet(expected), _split_sheet(cited)
    if not e or not c or e[0] != c[0]:
        return False
    try:
        e_min_col, e_min_row, e_max_col, e_max_row = range_boundaries(e[1])
        c_min_col, c_min_row, c_max_col, c_max_row = range_boundaries(c[1])
    except (ValueError, TypeError):
        return False
    return bool(
        c_min_col <= e_min_col
        and c_min_row <= e_min_row
        and e_max_col <= c_max_col
        and e_max_row <= c_max_row
    )


def citation_satisfied(required: Citation, transcript: Transcript) -> bool:
    return any(
        s.document == required.document
        and any(locator_matches(loc, s.locator) for loc in [required.locator, *required.also])
        for s in transcript.final.sources
    )


# --- numbers --------------------------------------------------------------------------------------


def _strip_currency(text: str) -> str:
    return re.sub(r"₹|Rs\.?|INR", "", text)


def figure_present(expected: str, text: str) -> bool:
    lib = grounding_lib()
    if lib is not None:
        try:  # Story 4.4's API; fall back if it differs from what this was written against
            wanted = lib.extract_figures(expected)
            in_answer = lib.build_grounding_set([text])
            return bool(wanted) and all(
                getattr(m, "matched", m) for m in (lib.match(w, in_answer) for w in wanted)
            )
        except (AttributeError, TypeError):
            pass
    return _squash(expected) in _squash(text)


def _squash(text: str) -> str:
    return "".join(_strip_currency(text).split())


def parse_number(token: str) -> Decimal | None:
    raw = token.strip()
    negative = (raw.startswith("(") and raw.endswith(")")) or raw.startswith("-")
    cleaned = raw.strip("()%").lstrip("-").replace(",", "").replace("₹", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if negative else value


def _decimals(token: str) -> int:
    cleaned = token.strip("()%").replace(",", "")
    return len(cleaned.split(".", 1)[1]) if "." in cleaned else 0


def numbers_in(text: str) -> list[str]:
    return [m.group(0) for m in NUMBER_RE.finditer(CITATION_TEXT_RE.sub(" ", text))]


def is_exempt(token: str) -> bool:
    value = parse_number(token)
    if value is None:
        return True
    plain = "," not in token and "." not in token and "%" not in token
    return plain and (abs(value) <= SMALL_COUNT or int(value) in YEAR_RANGE)


def grounded(token: str, pool: set[Decimal]) -> bool:
    """Equal at the answer's display precision (``1,579`` matches ``1,579.4``)."""
    value = parse_number(token)
    if value is None:
        return True
    places = Decimal(1).scaleb(-_decimals(token))
    with localcontext() as ctx:
        ctx.prec = 60  # identifiers and long references can exceed the default 28 digits
        return any(abs(source - value) <= places / 2 for source in pool)


def grounding_pool(
    case: Case, transcript: Transcript, reference: list[list[str]] | None
) -> set[Decimal]:
    texts: list[str] = [*case.turns, *case.expectations.must_contain_figures]
    for doc in case.documents:
        try:
            texts.append(corpus.full_text(doc))
        except corpus.CorpusMissingError:
            continue
    for turn in transcript.turns:
        for table in turn.tables:
            if table.origin != "text":  # engine/output tables are data; model text is not
                texts.extend(str(c) for row in table.rows for c in row)
    if reference:
        texts.extend(c for row in reference for c in row)
    pool = set()
    for text in texts:
        for token in NUMBER_RE.findall(str(text)):
            value = parse_number(token)
            if value is not None:
                pool.add(value)
    return pool


def ungrounded_figures(
    case: Case, transcript: Transcript, reference: list[list[str]] | None
) -> list[str]:
    tokens = [t for t in numbers_in(transcript.final.text) if not is_exempt(t)]
    if not tokens:
        return []
    pool = grounding_pool(case, transcript, reference)
    return [t for t in tokens if not grounded(t, pool)]


# --- reference outputs --------------------------------------------------------------------------


def load_reference(path: str) -> list[list[str]]:
    with (CASES_DIR / path).open(encoding="utf-8", newline="") as fh:
        return list(csv.reader(fh))


def _norm_header(name: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name).lower().replace("₹", "")).strip()


def _cell_equal(reference: str, value: Any) -> bool:
    ref_num = parse_number(reference) if NUMBER_RE.fullmatch(reference.strip()) else None
    if ref_num is not None:
        token = str(value).strip().replace("₹", "").replace("Rs.", "").strip()
        got = value if isinstance(value, int | float) else None
        candidate = Decimal(str(got)) if got is not None else parse_number(token)
        if candidate is None:
            return False
        places = Decimal(1).scaleb(-_decimals(reference))
        return abs(candidate - ref_num) <= places / 2 + Decimal("1e-9")
    return " ".join(str(value).split()).casefold() == " ".join(reference.split()).casefold()


def compare_table(reference: list[list[str]], table: Table, ordered: bool) -> tuple[bool, str]:
    header, ref_rows = reference[0], reference[1:]
    wanted = [_norm_header(h) for h in header]
    have = [_norm_header(c) for c in table.columns]
    if all(w in have for w in wanted):
        index = [have.index(w) for w in wanted]
    elif len(have) == len(wanted):
        index = list(range(len(wanted)))  # same shape, different labels: compare by position
    else:
        return False, f"columns {table.columns} do not cover {header}"
    rows = [[row[i] if i < len(row) else "" for i in index] for row in table.rows]
    if len(rows) != len(ref_rows):
        return False, f"{len(rows)} rows, reference has {len(ref_rows)}"
    if not ordered:
        key: Callable[[Sequence[Any]], str] = lambda r: " ".join(str(c) for c in r).casefold()  # noqa: E731
        rows, ref_rows = sorted(rows, key=key), sorted(ref_rows, key=key)
    for r, (ref_row, row) in enumerate(zip(ref_rows, rows, strict=True), start=2):
        for c, (ref_cell, cell) in enumerate(zip(ref_row, row, strict=True)):
            if not _cell_equal(ref_cell, cell):
                return False, f"row {r} column {header[c]!r}: expected {ref_cell!r}, got {cell!r}"
    return True, f"{len(ref_rows)} rows x {len(header)} columns match"


def reference_check(case: Case, transcript: Transcript, reference: list[list[str]]) -> Check:
    tables = sorted(
        transcript.final.tables, key=lambda t: {"output": 0, "event": 1, "text": 2}[t.origin]
    )
    details = []
    for table in tables:
        ok, detail = compare_table(reference, table, case.expectations.reference_ordered)
        if ok:
            return Check("reference_output", True, f"{table.origin} table: {detail}")
        details.append(f"{table.origin}: {detail}")
    if case.category == "excel_query":
        # A scalar answer may be given in text: every reference number must appear.
        numbers = set(numbers_in(transcript.final.text))
        ref_numbers = [c for row in reference[1:] for c in row if NUMBER_RE.fullmatch(c.strip())]
        if ref_numbers and all(any(_cell_equal(r, n) for n in numbers) for r in ref_numbers):
            return Check("reference_output", True, "reference values found in the answer text")
    return Check("reference_output", False, "; ".join(details) or "no table in the answer")


# --- the case ----------------------------------------------------------------------


def _contains(needle: str, text: str) -> bool:
    return " ".join(needle.split()).casefold() in " ".join(text.split()).casefold()


def _outcome_checks(case: Case, transcript: Transcript) -> list[Check]:
    """Stream errors, withheld answers, declines and not-found phrasing."""
    final, e = transcript.final, case.expectations
    checks: list[Check] = []

    if final.error:
        checks.append(Check("stream", False, f"error event: {final.error}"))
    declined = final.finish_reason == "declined"
    if final.finish_reason == "withheld" and not e.expect_decline:
        checks.append(Check("withheld", False, "answer withheld by the output gate"))
    if e.expect_decline:
        ok = declined or final.finish_reason == "withheld"
        category_ok = final.reason_code in (None, e.expect_decline)
        checks.append(
            Check(
                "decline",
                ok and category_ok,
                f"finish_reason={final.finish_reason} reason_code={final.reason_code}",
            )
        )
    elif declined:
        checks.append(Check("decline", False, f"unexpected decline ({final.reason_code})"))
    if e.expect_not_found:
        checks.append(
            Check(
                "not_found_phrase",
                bool(NOT_FOUND_RE.search(final.text)),
                "FR-030 phrasing" if NOT_FOUND_RE.search(final.text) else "phrase missing",
            )
        )
    return checks


def _content_checks(case: Case, transcript: Transcript) -> list[Check]:
    """Citations, figures and phrases."""
    final, e = transcript.final, case.expectations
    checks: list[Check] = []
    for required in e.must_cite:
        ok = citation_satisfied(required, transcript)
        got = ", ".join(f"{s.document}#{s.locator}" for s in final.sources) or "none"
        checks.append(
            Check("citation", ok, f"{required.document}#{required.locator} (cited: {got})")
        )
    for fig in e.must_contain_figures:
        checks.append(Check("figure", figure_present(fig, final.text), fig))
    for phrase in e.must_contain:
        checks.append(Check("contains", _contains(phrase, final.text), phrase))
    for phrase in e.must_not_contain:
        checks.append(Check("not_contains", not _contains(phrase, final.text), phrase))
    return checks


def score(case: Case, transcript: Transcript) -> CaseResult:
    result = CaseResult(case.id, case.category, "passed", latency_ms=transcript.final.latency_ms)
    if transcript.skipped:
        result.status, result.skipped_reason = "skipped", transcript.skipped
        return result
    if transcript.error:
        result.status, result.error = "error", transcript.error
        return result
    e = case.expectations
    checks = result.checks
    checks += _outcome_checks(case, transcript)
    checks += _content_checks(case, transcript)
    reference = load_reference(e.reference_output) if e.reference_output else None
    if reference is not None:
        check = reference_check(case, transcript, reference)
        checks.append(check)
        result.reference_match = check.passed
    if not (e.expect_decline or e.expect_not_found):
        result.ungrounded_figures = ungrounded_figures(case, transcript, reference)
        if result.ungrounded_figures:
            checks.append(
                Check(
                    "grounding", False, f"ungrounded: {', '.join(result.ungrounded_figures[:10])}"
                )
            )

    result.status = "failed" if result.failed_checks else "passed"
    return result
