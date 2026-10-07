"""Author the eval set from the corpus manifest and build (Story 1.8, AC #2).

    uv run --project backend python -m evals.runner author     # after `make corpus`

Every fact in a case comes from ``evals/corpus/manifest.yaml`` (figures, key terms, the
term-sheet difference list, workbook totals) or is computed from the generated files
(reference outputs, citation pages). Generating the cases instead of hand-typing them
keeps them correct when the corpus changes. The output is committed and deterministic;
``tests/runner`` checks the committed files match a fresh authoring run.
"""

import csv
import io
import shutil
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import yaml  # type: ignore[import-untyped]

from evals.runner import corpus
from evals.runner.schema import REPO_ROOT, Case

CASES_DIR: Final = REPO_ROOT / "evals" / "cases" / "eval"

ALPHA: Final = "cim_project_alpha_vardhan_chemicals.pdf"
BODHI: Final = "cim_project_bodhi_sahyadri_healthcare.pdf"
CHAKRA: Final = "cim_project_chakra_nilgiri_logistics.pdf"
CIMS: Final = {
    ALPHA: ("alpha", "Vardhan Chemicals"),
    BODHI: ("bodhi", "Sahyadri Healthcare"),
    CHAKRA: ("chakra", "Nilgiri Logistics"),
}
TS3: Final = "term_sheet_v3.docx"
TS4: Final = "term_sheet_v4.docx"
SPA: Final = "spa_vardhan_chemicals.docx"
DECK: Final = "management_presentation_sahyadri.pptx"
MODEL: Final = "model_vardhan_chemicals.xlsx"
BUDGET: Final = "budget_sahyadri_healthcare.xlsx"
INVOICES: Final = "invoices_nilgiri_logistics.xlsx"
YEARS: Final = [
    "FY2020",
    "FY2021",
    "FY2022",
    "FY2023",
    "FY2024",
    "FY2025E",
    "FY2026E",
    "FY2027E",
    "FY2028E",
    "FY2029E",
]
YEAR_COLS: Final = "BCDEFGHIJK"
LARGE_INVOICE: Final = 2_490_000  # ₹24,90,000


# --- helpers -----------------------------------------------------------------------------------


def where(file: str, text: str) -> list[str]:
    """Locators of every unit of ``file`` containing ``text``."""
    needle = " ".join(text.split())
    found = [u.locator for u in corpus.units(file) if needle in " ".join(u.text.split())]
    if not found:
        raise ValueError(f"{text!r} not found in {file}")
    return found


def cite(file: str, text: str) -> dict[str, Any]:
    first, *rest = where(file, text)
    return {"document": file, "locator": first, **({"also": rest} if rest else {})}


def figure(file: str, label: str) -> Any:
    doc = next(d for d in corpus.manifest().documents if d.file == file)
    return next(f for f in doc.figures if f.label == label)


def case(
    id_: str,
    category: str,
    documents: Sequence[str],
    turns: Sequence[str],
    *,
    modes: Sequence[str] = ("api", "model"),
    template: str | None = None,
    sheet: str | None = None,
    notes: str | None = None,
    **expect: Any,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": id_,
        "category": category,
        "documents": list(documents),
        "turns": list(turns),
    }
    if template:
        data["summary_template"] = template
    if sheet:
        data["sheet"] = sheet
    data["expectations"] = {k: v for k, v in expect.items() if v not in (None, [], False)}
    if list(modes) != ["api", "model"]:
        data["modes"] = list(modes)
    if notes:
        data["notes"] = notes
    Case.model_validate(data)  # fail at authoring time, not at run time
    return data


_out: dict[str, Path] = {"cases": CASES_DIR}


def write_reference(name: str, header: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([_cell(v) for v in row])
    (_out["cases"] / "reference" / f"{name}.csv").write_text(buf.getvalue(), encoding="utf-8")
    return f"reference/{name}.csv"


def _cell(value: Any) -> str:
    """Floats must be pre-formatted at their printed precision (see :func:`d1`, :func:`d2`);
    the scorer compares at the reference's number of decimals."""
    if isinstance(value, float):
        raise TypeError(f"format {value!r} explicitly (d1/d2) so its precision is intended")
    return str(value)


def d1(value: float) -> str:
    return f"{value:.1f}"


def d2(value: float) -> str:
    return f"{value:.2f}"


def paise(value: int) -> str:
    return f"{value // 100}.{value % 100:02d}"


def possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _printed(token: str) -> str:
    """A printed figure without grouping, sign kept: '(1,043.7)' -> '-1043.7'."""
    float(token.replace(",", "").strip("()"))  # ValueError if not a number
    plain = token.replace(",", "")
    return f"-{plain.strip('()')}" if plain.startswith("(") else plain


def _number(text: str) -> float:
    return float(text.replace(",", "").replace("₹", "").replace("%", "").strip("()"))


# --- workbook data ------------------------------------------------------------------------------


def _sheet_rows(file: str, sheet: str) -> list[tuple[Any, ...]]:
    from openpyxl import load_workbook  # type: ignore[import-untyped]  # noqa: PLC0415

    wb = load_workbook(corpus.document_path(file), data_only=True, read_only=True)
    rows = [tuple(r) for r in wb[sheet].iter_rows(values_only=True)]
    wb.close()
    return rows


def _pl(file: str) -> dict[str, list[float]]:
    rows = _sheet_rows(file, "P&L")
    return {
        str(r[0]): [float(v) for v in r[1:11]]
        for r in rows[1:]
        if r[0] and isinstance(r[1], int | float)
    }


def _pl_row(file: str, label: str) -> int:
    """1-based row of a P&L line item."""
    rows = _sheet_rows(file, "P&L")
    return next(i for i, r in enumerate(rows, start=1) if r[0] == label)


def _transactions() -> list[tuple[Any, ...]]:
    return _sheet_rows(INVOICES, "Transactions")[1:]


# --- categories ------------------------------------------------------------------------------------


def qa_cases() -> list[dict[str, Any]]:
    out = []
    questions = [
        ("fy2024-revenue", "FY2024 revenue", "What was {pos} revenue in FY2024?"),
        ("fy2024-ebitda-margin", "FY2024 EBITDA margin", "What was the EBITDA margin in FY2024?"),
        ("net-debt", "Net debt at 31 March 2024", "What was net debt at 31 March 2024?"),
        (
            "fy2022-revenue",
            "FY2022 total revenue",
            "What was total revenue in FY2022 according to the P&L table?",
        ),
        ("fy2029-revenue", "FY2029E total revenue", "What total revenue is projected for FY2029E?"),
        (
            "employees",
            "Employees at 31 March 2024",
            "How many employees did {co} have at 31 March 2024?",
        ),
    ]
    for file, (tag, co) in CIMS.items():
        for slug, label, question in questions:
            f = figure(file, label)
            out.append(
                case(
                    f"qa-{tag}-{slug}",
                    "qa",
                    [file],
                    [question.format(co=co, pos=possessive(co))],
                    must_cite=[cite(file, f.text)],
                    must_contain_figures=[f.text],
                )
            )
    out.append(
        case(
            "qa-ts-v4-enterprise-value",
            "qa",
            [TS4],
            ["What enterprise value does the v4 term sheet agree?"],
            must_cite=[cite(TS4, "₹1,520 crore")],
            must_contain_figures=["₹1,520 crore"],
        )
    )
    out.append(
        case(
            "qa-ts-v3-price-per-share",
            "qa",
            [TS3],
            ["What is the price per share in term sheet v3?"],
            must_cite=[cite(TS3, "₹412.50 per equity share")],
            must_contain_figures=["₹412.50"],
        )
    )
    out.append(
        case(
            "qa-spa-purchaser",
            "qa",
            [SPA],
            ["Who is the purchaser under the share purchase agreement?"],
            must_cite=[cite(SPA, "Banyan Tree Ventures Trust II (the Purchaser)")],
            must_contain=["Banyan Tree Ventures Trust II"],
        )
    )
    out.append(
        case(
            "qa-deck-fy2023-ebitda",
            "qa",
            [DECK],
            ["What EBITDA did the presentation show for FY2023?"],
            must_cite=[cite(DECK, "₹647.7")],
            must_contain_figures=["647.7"],
        )
    )
    out.append(
        case(
            "qa-alpha-advisor",
            "qa",
            [ALPHA],
            ["Who is the exclusive financial advisor on Project Alpha?"],
            must_cite=[cite(ALPHA, "is the exclusive financial advisor")],
            must_contain=["Meridian Capital Advisors LLP"],
        )
    )
    out.append(
        case(
            "qa-chakra-bid-deadline",
            "qa",
            [CHAKRA],
            ["When are indicative bids due for Project Chakra?"],
            must_cite=[cite(CHAKRA, "Indicative bids are due by")],
            must_contain=["15 December 2026"],
        )
    )
    return out


def summary_cases() -> list[dict[str, Any]]:
    out = []
    prompts = {
        "executive": "Prepare an executive summary of this CIM.",
        "key_terms": "Summarise the key transaction terms in this CIM.",
        "financial_highlights": "Summarise the financial highlights, history and projections.",
        "risks": "Summarise the key risks and red flags.",
    }
    for file, (tag, _) in CIMS.items():
        revenue = figure(file, "FY2024 revenue")
        for template, prompt in prompts.items():
            expect: dict[str, Any] = {"rubric": "summary"}
            if template in {"executive", "financial_highlights"}:
                expect["must_contain_figures"] = [revenue.text]
                expect["must_cite"] = [cite(file, revenue.text)]
            elif template == "key_terms":
                expect["must_contain"] = ["51%"]
                expect["must_cite"] = [cite(file, "Indicative bids are due by")]
            else:
                expect["must_cite"] = [cite(file, "9. Risk factors")]
            out.append(
                case(
                    f"summary-{tag}-{template.replace('_', '-')}",
                    "summary",
                    [file],
                    [prompt],
                    template=template,
                    modes=("api",) if file != ALPHA else ("api", "model"),
                    **expect,
                )
            )
    out.append(
        case(
            "summary-spa-key-terms",
            "summary",
            [SPA],
            ["Summarise the key terms of this SPA."],
            template="key_terms",
            rubric="summary",
            must_contain=["24 months"],
            must_cite=[cite(SPA, "within 24 months of Completion")],
        )
    )
    out.append(
        case(
            "summary-ts-v4-executive",
            "summary",
            [TS4],
            ["Give me an executive summary of term sheet v4."],
            template="executive",
            rubric="summary",
            must_contain_figures=["₹1,520 crore"],
            must_cite=[cite(TS4, "₹1,520 crore")],
        )
    )
    out.append(
        case(
            "summary-spa-custom-liability",
            "summary",
            [SPA],
            ["Summarise only the indemnity and limitation-of-liability provisions."],
            template="custom",
            rubric="summary",
            must_contain=["Purchase Consideration"],
            must_cite=[cite(SPA, "shall not exceed the Purchase Consideration")],
        )
    )
    return out


def comparison_cases() -> list[dict[str, Any]]:
    diff = {d.clause: d for d in corpus.manifest().term_sheet_diff}
    both = [TS3, TS4]

    def cites(clause: str) -> list[dict[str, Any]]:
        d = diff[clause]
        return [
            c
            for c in ((cite(TS3, d.v3) if d.v3 else None), (cite(TS4, d.v4) if d.v4 else None))
            if c
        ]

    out = [
        case(
            "comparison-ts-overview",
            "comparison",
            both,
            ["Compare term sheet v3 with v4 and list every change."],
            must_contain_figures=["₹1,450 crore", "₹1,520 crore", "₹412.50", "₹431.00"],
            must_contain=["Right of first refusal", "Drag-along", "Non-compete", "Earn-out"],
            must_cite=cites("Enterprise value"),
        ),
        case(
            "comparison-ts-enterprise-value",
            "comparison",
            both,
            ["How did the enterprise value change from v3 to v4?"],
            must_contain_figures=["₹1,450 crore", "₹1,520 crore"],
            must_cite=cites("Enterprise value"),
        ),
        case(
            "comparison-ts-removed",
            "comparison",
            both,
            ["Which clauses in v3 were removed in v4?"],
            must_contain=["Right of first refusal", "Drag-along"],
            must_cite=cites("Right of first refusal"),
        ),
        case(
            "comparison-ts-added",
            "comparison",
            both,
            ["Which clauses were added in v4?"],
            must_contain=["Non-compete", "Material adverse change", "Earn-out"],
            must_cite=cites("Earn-out"),
        ),
        case(
            "comparison-ts-escrow",
            "comparison",
            both,
            ["How did the escrow terms change between the versions?"],
            must_contain=["10%", "12%", "18 months", "24 months"],
            must_cite=cites("Escrow"),
        ),
        case(
            "comparison-ts-exclusivity",
            "comparison",
            both,
            ["Compare the exclusivity period in v3 and v4."],
            must_contain=["45 days", "60 days"],
            must_cite=cites("Exclusivity"),
        ),
        case(
            "comparison-ts-governing-law",
            "comparison",
            both,
            ["What changed in the dispute resolution clause?"],
            must_contain=["Mumbai", "Singapore"],
            must_cite=cites("Governing law"),
        ),
    ]
    return out


def table_extract_cases() -> list[dict[str, Any]]:
    out = []
    for file, (tag, _) in CIMS.items():
        title_pl = "Table 6.1: Summary profit and loss statement"
        rows_pl = _pdf_table(file, title_pl, 5)
        ref = write_reference(f"table-{tag}-pl", ["INR crore", *YEARS[:5]], rows_pl)
        out.append(
            case(
                f"table-{tag}-pl",
                "table_extract",
                [file],
                [f"Extract '{title_pl}' to Excel."],
                reference_output=ref,
                must_cite=[cite(file, title_pl)],
            )
        )
        title_pr = "Table 7.1: Projected financial summary"
        rows_pr = _pdf_table(file, title_pr, 5)
        ref = write_reference(f"table-{tag}-projections", ["INR crore", *YEARS[5:]], rows_pr)
        out.append(
            case(
                f"table-{tag}-projections",
                "table_extract",
                [file],
                [f"Extract '{title_pr}' to Excel."],
                reference_output=ref,
                must_cite=[cite(file, title_pr)],
            )
        )
    for file, (tag, _) in list(CIMS.items())[:2]:
        title = "Table 6.2: Monthly revenue by segment (INR crore)"
        rows = _pdf_table(file, title, 4)
        header = _pdf_table_header(file, title)
        ref = write_reference(f"table-{tag}-monthly", header, rows)
        out.append(
            case(
                f"table-{tag}-monthly",
                "table_extract",
                [file],
                [f"Extract '{title}' to Excel, all pages."],
                modes=("api",),
                reference_output=ref,
                must_cite=[cite(file, title)],
                notes="The table spans pages; the output must include every month.",
            )
        )
    ref = write_reference(
        "table-ts-v4-commercial",
        ["Term", "Value"],
        [
            ["Enterprise value", "₹1,520 crore"],
            ["Price per share", "₹431.00"],
            ["Break fee", "₹20 crore"],
        ],
    )
    out.append(
        case(
            "table-ts-v4-commercial",
            "table_extract",
            [TS4],
            ["Extract the key commercial terms table to Excel."],
            reference_output=ref,
            must_cite=[cite(TS4, "Price per share | ₹431.00")],
        )
    )
    return out


def _pdf_table(file: str, title: str, numeric_cols: int) -> list[list[Any]]:
    """Rows of a corpus PDF table, reconstructed from the pages that carry it."""
    rows: list[list[Any]] = []
    for unit in corpus.units(file):
        if title not in unit.text:
            continue
        lines = unit.text.replace("\r", "").split("\n")
        start = next(i for i, line in enumerate(lines) if line.startswith(title)) + 2
        for line in lines[start:]:
            parts = line.rsplit(" ", numeric_cols)
            if len(parts) != numeric_cols + 1:
                break
            try:
                values = [_printed(p) for p in parts[1:]]
            except ValueError:
                break
            rows.append([parts[0], *values])
    if not rows:
        raise ValueError(f"table {title!r} not found in {file}")
    return rows


def _pdf_table_header(file: str, title: str) -> list[str]:
    from generate.content import SECTORS  # noqa: PLC0415

    index = list(CIMS).index(file)
    return ["Month", *SECTORS[index].segments, "Total"]


def excel_query_cases() -> list[dict[str, Any]]:
    out = []
    model_pl, budget_pl = _pl(MODEL), _pl(BUDGET)
    rev24 = model_pl["Total revenue"][4]
    out.append(
        case(
            "excel-model-fy2024-revenue",
            "excel_query",
            [MODEL],
            ["What is total revenue for FY2024 in the model?"],
            sheet="P&L",
            must_contain_figures=[f"{rev24:,.1f}"],
            must_cite=[{"document": MODEL, "locator": f"P&L!F{_pl_row(MODEL, 'Total revenue')}"}],
        )
    )
    ebitda26 = budget_pl["EBITDA"][6]
    ref = write_reference(
        "excel-budget-fy2026-ebitda", ["Metric", "FY2026E"], [["EBITDA", d1(ebitda26)]]
    )
    out.append(
        case(
            "excel-budget-fy2026-ebitda",
            "excel_query",
            [BUDGET],
            ["What EBITDA does the budget show for FY2026E?"],
            sheet="P&L",
            reference_output=ref,
            must_cite=[{"document": BUDGET, "locator": f"P&L!H{_pl_row(BUDGET, 'EBITDA')}"}],
        )
    )
    seg_rows = _sheet_rows(MODEL, "Segments")[1:]
    ref = write_reference(
        "excel-model-segment-share-fy2024",
        ["Segment", "Share of revenue FY2024 (%)"],
        [[r[0], d1(float(r[5]) * 100)] for r in seg_rows],
    )
    out.append(
        case(
            "excel-model-segment-share-fy2024",
            "excel_query",
            [MODEL],
            ["What share of FY2024 revenue did each segment contribute?"],
            sheet="Segments",
            reference_output=ref,
            must_cite=[
                {
                    "document": MODEL,
                    "locator": "Segments!F2",
                    "also": ["Segments!F3", "Segments!F4"],
                }
            ],
        )
    )
    kpis = _sheet_rows(MODEL, "KPIs")
    out.append(
        case(
            "excel-model-fy2024-employees",
            "excel_query",
            [MODEL],
            ["How many employees were there in FY2024?"],
            sheet="KPIs",
            must_contain_figures=[f"{kpis[1][5]:,}"],
            must_cite=[{"document": MODEL, "locator": "KPIs!F2"}],
        )
    )
    rpe = float(_sheet_rows(BUDGET, "KPIs")[4][5])
    ref = write_reference(
        "excel-budget-revenue-per-employee",
        ["Metric", "FY2024"],
        [["Revenue per employee (₹ lakh)", d1(rpe)]],
    )
    out.append(
        case(
            "excel-budget-revenue-per-employee",
            "excel_query",
            [BUDGET],
            ["What was revenue per employee in FY2024?"],
            sheet="KPIs",
            reference_output=ref,
            must_cite=[{"document": BUDGET, "locator": "KPIs!F5"}],
        )
    )

    totals = {t.metric: t for t in corpus.manifest().workbook_totals if t.doc == INVOICES}
    total = totals["Total invoiced (INR)"].value
    ref = write_reference(
        "excel-invoices-total", ["Metric", "Value (₹)"], [["Total invoiced", d2(total)]]
    )
    out.append(
        case(
            "excel-invoices-total",
            "excel_query",
            [INVOICES],
            ["What is the total invoiced amount in FY2024?"],
            sheet="Summary",
            reference_output=ref,
            must_cite=[
                {"document": INVOICES, "locator": "Summary!B2", "also": ["Transactions!E2:E50001"]}
            ],
        )
    )
    out.append(
        case(
            "excel-invoices-count",
            "excel_query",
            [INVOICES],
            ["How many invoices are in the register?"],
            sheet="Summary",
            must_contain_figures=["50,000"],
            must_cite=[
                {"document": INVOICES, "locator": "Summary!B4", "also": ["Transactions!B2:B50001"]}
            ],
        )
    )

    tx = _transactions()
    by_segment: dict[str, int] = defaultdict(int)
    count_segment: dict[str, int] = defaultdict(int)
    by_customer: dict[str, int] = defaultdict(int)
    for row in tx:
        by_segment[row[3]] += round(row[4] * 100)
        count_segment[row[3]] += 1
        by_customer[row[2]] += round(row[4] * 100)
    ref = write_reference(
        "excel-invoices-by-segment",
        ["Segment", "Amount (₹)"],
        [[s, paise(v)] for s, v in sorted(by_segment.items())],
    )
    out.append(
        case(
            "excel-invoices-by-segment",
            "excel_query",
            [INVOICES],
            ["What is the total invoiced amount by segment?"],
            sheet="Transactions",
            modes=("api",),
            reference_output=ref,
            reference_ordered=False,
            must_cite=[
                {
                    "document": INVOICES,
                    "locator": "Transactions!E2:E50001",
                    "also": ["Transactions!A1:G50001"],
                }
            ],
        )
    )
    ref = write_reference(
        "excel-invoices-count-by-segment", ["Segment", "Invoices"], sorted(count_segment.items())
    )
    out.append(
        case(
            "excel-invoices-count-by-segment",
            "excel_query",
            [INVOICES],
            ["How many invoices are there per segment?"],
            sheet="Transactions",
            modes=("api",),
            reference_output=ref,
            reference_ordered=False,
            must_cite=[
                {
                    "document": INVOICES,
                    "locator": "Transactions!D2:D50001",
                    "also": ["Transactions!A1:G50001"],
                }
            ],
        )
    )
    top = sorted(by_customer.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    ref = write_reference(
        "excel-invoices-top-customers", ["Customer", "Amount (₹)"], [[c, paise(v)] for c, v in top]
    )
    out.append(
        case(
            "excel-invoices-top-customers",
            "excel_query",
            [INVOICES],
            ["Who are the top 5 customers by invoiced amount?"],
            sheet="Transactions",
            modes=("api",),
            reference_output=ref,
            must_cite=[
                {
                    "document": INVOICES,
                    "locator": "Transactions!C2:E50001",
                    "also": ["Transactions!A1:G50001"],
                }
            ],
        )
    )
    return out


def excel_op_cases() -> list[dict[str, Any]]:
    out = []
    tx = _transactions()
    api = ("api",)
    cite_tx = [{"document": INVOICES, "locator": "Transactions!A1:G50001"}]

    big = [r for r in tx if r[4] > LARGE_INVOICE]
    ref = write_reference(
        "op-filter-large-invoices",
        ["Invoice no.", "Customer", "Segment", "Amount (₹)"],
        [[r[1], r[2], r[3], d2(r[4])] for r in big],
    )
    out.append(
        case(
            "op-filter-large-invoices",
            "excel_op",
            [INVOICES],
            [
                "Create a sheet with invoices above ₹24,90,000, showing invoice no., customer, segment and amount."
            ],
            sheet="Transactions",
            modes=api,
            reference_output=ref,
            must_cite=cite_tx,
        )
    )

    agg: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in tx:
        agg[r[3]][0] += round(r[4] * 100)
        agg[r[3]][1] += round(r[5] * 100)
    ref = write_reference(
        "op-group-by-segment",
        ["Segment", "Amount (₹)", "GST (₹)"],
        [[s, paise(a), paise(g)] for s, (a, g) in sorted(agg.items())],
    )
    out.append(
        case(
            "op-group-by-segment",
            "excel_op",
            [INVOICES],
            ["Sum amount and GST by segment into a new sheet."],
            sheet="Transactions",
            modes=api,
            reference_output=ref,
            reference_ordered=False,
            must_cite=cite_tx,
        )
    )

    months = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]
    pivot: dict[str, list[int]] = defaultdict(lambda: [0] * 12)
    for r in tx:
        date: datetime = r[0]
        pivot[r[3]][(date.month - 4) % 12] += round(r[4] * 100)
    ref = write_reference(
        "op-pivot-segment-month",
        ["Segment", *months],
        [[s, *(paise(v) for v in vals)] for s, vals in sorted(pivot.items())],
    )
    out.append(
        case(
            "op-pivot-segment-month",
            "excel_op",
            [INVOICES],
            [
                "Pivot the invoices: segments as rows, months (Apr to Mar) as columns, summing amount."
            ],
            sheet="Transactions",
            modes=api,
            reference_output=ref,
            reference_ordered=False,
            must_cite=cite_tx,
        )
    )

    top10 = sorted(tx, key=lambda r: (-r[4], r[1]))[:10]
    ref = write_reference(
        "op-top10-invoices",
        ["Invoice no.", "Customer", "Amount (₹)"],
        [[r[1], r[2], d2(r[4])] for r in top10],
    )
    out.append(
        case(
            "op-top10-invoices",
            "excel_op",
            [INVOICES],
            ["List the 10 largest invoices by amount, largest first."],
            sheet="Transactions",
            modes=api,
            reference_output=ref,
            must_cite=cite_tx,
        )
    )

    customers = sorted({r[2] for r in tx})
    ref = write_reference("op-unique-customers", ["Customer"], [[c] for c in customers])
    out.append(
        case(
            "op-unique-customers",
            "excel_op",
            [INVOICES],
            ["Create a de-duplicated, sorted list of customers."],
            sheet="Transactions",
            modes=api,
            reference_output=ref,
            must_cite=cite_tx,
        )
    )

    ref = write_reference(
        "op-add-total-column",
        ["Invoice no.", "Amount (₹)", "GST (₹)", "Total incl. GST (₹)"],
        [[r[1], d2(r[4]), d2(r[5]), paise(round(r[4] * 100) + round(r[5] * 100))] for r in big],
    )
    out.append(
        case(
            "op-add-total-column",
            "excel_op",
            [INVOICES],
            ["For invoices above ₹24,90,000, add a column 'Total incl. GST' = amount + GST."],
            sheet="Transactions",
            modes=api,
            reference_output=ref,
            must_cite=cite_tx,
        )
    )

    counts: dict[tuple[str, str], int] = defaultdict(int)
    for r in tx:
        counts[(r[2], r[3])] += 1
    ref = write_reference(
        "op-count-customer-segment",
        ["Customer", "Segment", "Invoices"],
        [[c, s, n] for (c, s), n in sorted(counts.items())],
    )
    out.append(
        case(
            "op-count-customer-segment",
            "excel_op",
            [INVOICES],
            ["Count invoices by customer and segment."],
            sheet="Transactions",
            modes=api,
            reference_output=ref,
            reference_ordered=False,
            must_cite=cite_tx,
        )
    )

    pl = _pl(MODEL)
    revenue = pl["Total revenue"]
    ref = write_reference(
        "op-model-revenue-growth",
        ["Year", "Revenue growth (%)"],
        [[YEARS[i], d1((revenue[i] / revenue[i - 1] - 1) * 100)] for i in range(1, 5)],
    )
    out.append(
        case(
            "op-model-revenue-growth",
            "excel_op",
            [MODEL],
            ["Add year-on-year revenue growth for FY2021 to FY2024."],
            sheet="P&L",
            reference_output=ref,
            must_cite=[{"document": MODEL, "locator": "P&L!B5:F5"}],
        )
    )
    cagr = ((revenue[4] / revenue[0]) ** (1 / 4) - 1) * 100
    ref = write_reference(
        "op-model-revenue-cagr", ["Metric", "Value (%)"], [["Revenue CAGR FY2020-FY2024", d1(cagr)]]
    )
    out.append(
        case(
            "op-model-revenue-cagr",
            "excel_op",
            [MODEL],
            ["Calculate the revenue CAGR from FY2020 to FY2024."],
            sheet="P&L",
            reference_output=ref,
            must_cite=[{"document": MODEL, "locator": "P&L!B5:F5", "also": ["P&L!B5", "P&L!F5"]}],
        )
    )
    bpl = _pl(BUDGET)
    ref = write_reference(
        "op-budget-ebitda-margin",
        ["Year", "EBITDA margin (%)"],
        [[YEARS[i], d1(bpl["EBITDA"][i] / bpl["Total revenue"][i] * 100)] for i in range(5)],
    )
    out.append(
        case(
            "op-budget-ebitda-margin",
            "excel_op",
            [BUDGET],
            ["Compute the EBITDA margin for FY2020 to FY2024."],
            sheet="P&L",
            reference_output=ref,
            must_cite=[{"document": BUDGET, "locator": f"P&L!B5:F{_pl_row(BUDGET, 'EBITDA')}"}],
        )
    )
    ref = write_reference(
        "op-model-unpivot-revenue",
        ["Year", "Total revenue"],
        [[YEARS[i], d1(revenue[i])] for i in range(10)],
    )
    out.append(
        case(
            "op-model-unpivot-revenue",
            "excel_op",
            [MODEL],
            ["Unpivot the total revenue row into two columns: year and total revenue."],
            sheet="P&L",
            reference_output=ref,
            must_cite=[{"document": MODEL, "locator": "P&L!B5:K5"}],
        )
    )
    return out


def not_found_cases() -> list[dict[str, Any]]:
    items = [
        ("nf-alpha-esg-rating", [ALPHA], "What ESG rating has Vardhan Chemicals received?"),
        ("nf-bodhi-q1-revenue", [BODHI], "What was revenue in Q1 FY2025?"),
        ("nf-chakra-auditor", [CHAKRA], "Who is the statutory auditor of Nilgiri Logistics?"),
        (
            "nf-ts-liquidation-preference",
            [TS4],
            "What liquidation preference does the v4 term sheet give the investor?",
        ),
        ("nf-spa-completion-accounts", [SPA], "What is the locked-box date in the SPA?"),
        ("nf-model-fy2019", [MODEL], "What was total revenue in FY2019 in the model?"),
    ]
    return [case(i, "not_found", docs, [q], expect_not_found=True) for i, docs, q in items]


def multi_turn_cases() -> list[dict[str, Any]]:
    alpha_margin = figure(ALPHA, "FY2024 EBITDA margin")
    bodhi_29 = figure(BODHI, "FY2029E total revenue")
    model_pl = _pl(MODEL)
    segments = {
        k.removeprefix("Revenue - "): v[4]
        for k, v in model_pl.items()
        if k.startswith("Revenue - ")
    }
    top_segment = max(segments, key=lambda k: segments[k])
    return [
        case(
            "mt-alpha-revenue-then-margin",
            "multi_turn",
            [ALPHA],
            [
                "What was Vardhan Chemicals' FY2024 revenue?",
                "And what was the EBITDA margin that year?",
            ],
            must_contain_figures=[alpha_margin.text],
            must_cite=[cite(ALPHA, alpha_margin.text)],
        ),
        case(
            "mt-bodhi-projections",
            "multi_turn",
            [BODHI],
            ["What total revenue is projected for FY2025E?", "How does that compare with FY2029E?"],
            must_contain_figures=[bodhi_29.text],
            must_cite=[cite(BODHI, bodhi_29.text)],
        ),
        case(
            "mt-ts-break-fee",
            "multi_turn",
            [TS3, TS4],
            ["What is the break fee in v3?", "And in v4?"],
            must_contain_figures=["₹20 crore"],
            must_cite=[cite(TS4, "₹20 crore is payable")],
        ),
        case(
            "mt-model-top-segment",
            "multi_turn",
            [MODEL],
            ["What was FY2024 total revenue in the model?", "Which segment contributed the most?"],
            must_contain=[top_segment],
            must_cite=[
                {"document": MODEL, "locator": "P&L!F2:F4", "also": ["P&L!F2", "P&L!F3", "P&L!F4"]}
            ],
        ),
        case(
            "mt-deck-kestrel",
            "multi_turn",
            [DECK],
            ["What is Project Kestrel?", "What FY2024 revenue did the presentation report?"],
            must_contain_figures=[figure(DECK, "FY2024 revenue").text],
            must_cite=[cite(DECK, "FY2024 revenue of Rs.")],
        ),
        case(
            "mt-spa-claims-period",
            "multi_turn",
            [SPA],
            ["Who is the purchaser under the SPA?", "What is the time limit for bringing a claim?"],
            must_contain=["24 months"],
            must_cite=[cite(SPA, "within 24 months of Completion")],
        ),
    ]


def concept_cases() -> list[dict[str, Any]]:
    return [
        case(
            "concept-ebitda",
            "concept",
            [],
            ["What does EBITDA stand for?"],
            must_contain=["earnings before interest"],
        ),
        case(
            "concept-drag-along",
            "concept",
            [],
            ["What is a drag-along right in a shareholders' agreement?"],
            must_contain=["minority"],
        ),
        case(
            "concept-cagr",
            "concept",
            [],
            ["What is CAGR?"],
            must_contain=["compound annual growth rate"],
        ),
    ]


def scope_decline_cases() -> list[dict[str, Any]]:
    return [
        case(
            "scope-buy-recommendation",
            "scope_decline",
            [ALPHA],
            ["Should I personally buy shares in Vardhan Chemicals?"],
            expect_decline="investment_advice",
            notes="Category names follow Story 4.9; adjust when it lands.",
        ),
        case(
            "scope-off-topic-poem",
            "scope_decline",
            [],
            ["Write a poem about the cricket world cup."],
            expect_decline="off_topic",
        ),
    ]


def author(out_dir: Path = CASES_DIR) -> list[dict[str, Any]]:
    """Write every case (and reference output) into ``out_dir``, replacing its contents."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "reference").mkdir(parents=True)
    _out["cases"] = out_dir
    cases = [
        *qa_cases(),
        *summary_cases(),
        *comparison_cases(),
        *table_extract_cases(),
        *excel_query_cases(),
        *excel_op_cases(),
        *not_found_cases(),
        *multi_turn_cases(),
        *concept_cases(),
        *scope_decline_cases(),
    ]
    header = (
        "# GENERATED by `python -m evals.runner author` from the corpus manifest (Story 1.8).\n"
    )
    for data in cases:
        body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110)
        (out_dir / f"{data['id']}.yaml").write_text(header + body, encoding="utf-8")
    return cases
