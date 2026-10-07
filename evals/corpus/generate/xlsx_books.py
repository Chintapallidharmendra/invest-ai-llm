"""Financial workbooks (XLSX) and a vendor ledger (CSV).

Every workbook has P&L, Segments and KPIs sheets with formulas whose cached values are
written (the app reads cached values only, ADR-030). Across the set there is exactly one
formula without a cached value, one hidden sheet, cell-comment injections and one sheet
with 50,000 data rows.
"""

import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from openpyxl import Workbook  # type: ignore[import-untyped]
from openpyxl.comments import Comment  # type: ignore[import-untyped]
from openpyxl.utils import get_column_letter  # type: ignore[import-untyped]
from openpyxl.worksheet.worksheet import Worksheet  # type: ignore[import-untyped]

from generate.content import (
    SYNTHETIC_BANNER,
    Company,
    DocRecord,
    Injections,
    canary_for,
    faker_for,
    make_people,
    rng_for,
)
from generate.financials import HISTORY, YEARS, Financials, fmt_inr
from generate.ids import TEST_CARDS, IdFactory, spaced
from generate.manifest_schema import WorkbookTotal
from generate.ooxml import add_cached_values, normalize

FIXED_TIME: Final = datetime(2026, 9, 30, 10, 0, 0)
TRANSACTION_ROWS: Final = 50_000
YEAR_COLS: Final = [get_column_letter(2 + i) for i in range(len(YEARS))]  # B..K


@dataclass
class Book:
    """A workbook under construction plus the cached values of its formulas."""

    wb: Workbook
    cached: dict[str, dict[str, float]] = field(default_factory=dict)  # sheet -> ref -> value
    uncached: dict[str, set[str]] = field(default_factory=dict)

    def formula(self, ws: Worksheet, ref: str, formula: str, value: float | None) -> None:
        ws[ref] = formula
        if value is None:
            self.uncached.setdefault(ws.title, set()).add(ref)
        else:
            self.cached.setdefault(ws.title, {})[ref] = value

    def save(self, path: Path) -> None:
        self.wb.save(path)
        patches = {}
        for index, ws in enumerate(self.wb.worksheets, start=1):
            values = self.cached.get(ws.title, {})
            uncached = self.uncached.get(ws.title, set())
            if values or uncached:
                patches[f"xl/worksheets/sheet{index}.xml"] = add_cached_values(values, uncached)
        normalize(path, patches)


def _new_book(title: str, description: str) -> Book:
    wb = Workbook()
    props = wb.properties
    props.creator = props.lastModifiedBy = "Synthetic Corpus"
    props.title = title
    props.description = description
    props.created = props.modified = FIXED_TIME
    return Book(wb)


def _pl_sheet(book: Book, fin: Financials, rec: DocRecord) -> dict[str, int]:
    """P&L with data rows as values and subtotals as formulas. Returns label -> row."""
    ws = book.wb.active
    assert ws is not None
    ws.title = "P&L"
    ws["A1"] = "INR crore"
    for col, year in zip(YEAR_COLS, YEARS, strict=True):
        ws[f"{col}1"] = year
    rows: dict[str, int] = {}
    for r, (label, _) in enumerate(fin.pl_rows(), start=2):
        rows[label] = r
        ws[f"A{r}"] = label
    seg_rows = [rows[f"Revenue - {n}"] for n in fin.segments]
    formulas = {
        "Total revenue": lambda c: f"=SUM({c}{seg_rows[0]}:{c}{seg_rows[-1]})",
        "Gross profit": lambda c: f"={c}{rows['Total revenue']}-{c}{rows['Cost of goods sold']}",
        "EBITDA": lambda c: (
            f"={c}{rows['Gross profit']}-{c}{rows['Employee costs']}"
            f"-{c}{rows['Other operating expenses']}"
        ),
        "EBIT": lambda c: f"={c}{rows['EBITDA']}-{c}{rows['Depreciation and amortisation']}",
        "Profit before tax": lambda c: f"={c}{rows['EBIT']}-{c}{rows['Finance costs']}",
        "Profit after tax": lambda c: f"={c}{rows['Profit before tax']}-{c}{rows['Tax']}",
    }
    for label, values in fin.pl_rows():
        r = rows[label]
        for i, col in enumerate(YEAR_COLS):
            if label in formulas:
                book.formula(ws, f"{col}{r}", formulas[label](col), values[i] / 10)
            else:
                ws[f"{col}{r}"] = values[i] / 10
            ws[f"{col}{r}"].number_format = "#,##0.0"
    ws.column_dimensions["A"].width = 32
    last = len(fin.pl_rows()) + 3
    ws[f"A{last}"] = SYNTHETIC_BANNER
    ws[f"A{last + 1}"] = f"Model reference {rec.canary}"
    rec.plant("canary", "canary", rec.canary, f"P&L!A{last + 1}")
    return rows


def _segments_sheet(book: Book, fin: Financials, rows: dict[str, int]) -> None:
    ws = book.wb.create_sheet("Segments")
    ws["A1"] = "Share of revenue"
    for col, year in zip(YEAR_COLS, YEARS, strict=True):
        ws[f"{col}1"] = year
    total_row = rows["Total revenue"]
    for r, name in enumerate(fin.segments, start=2):
        ws[f"A{r}"] = name
        seg_row = rows[f"Revenue - {name}"]
        for i, col in enumerate(YEAR_COLS):
            share = fin.segments[name][i] / fin.revenue(i)
            book.formula(ws, f"{col}{r}", f"='P&L'!{col}{seg_row}/'P&L'!{col}{total_row}", share)
            ws[f"{col}{r}"].number_format = "0.0%"


def _kpi_sheet(book: Book, fin: Financials, rows: dict[str, int], uncached: str | None) -> None:
    ws = book.wb.create_sheet("KPIs")
    ws["A1"] = "KPI"
    for col, year in zip(YEAR_COLS, YEARS, strict=True):
        ws[f"{col}1"] = year
    ws["A2"], ws["A3"], ws["A4"], ws["A5"], ws["A6"] = (
        "Employees",
        "Sector KPI (index)",
        "EBITDA margin",
        "Revenue per employee (₹ lakh)",
        "Net debt / EBITDA (x)",
    )
    ws["A7"] = "Net debt (₹ crore)"
    for i, col in enumerate(YEAR_COLS):
        ws[f"{col}2"] = fin.employees[i]
        ws[f"{col}3"] = fin.kpi[i] / 10
        ws[f"{col}7"] = fin.net_debt[i] / 10
        rev, ebitda = rows["Total revenue"], rows["EBITDA"]
        book.formula(
            ws, f"{col}4", f"='P&L'!{col}{ebitda}/'P&L'!{col}{rev}", fin.ebitda(i) / fin.revenue(i)
        )
        ws[f"{col}4"].number_format = "0.0%"
        book.formula(
            ws, f"{col}5", f"='P&L'!{col}{rev}*100/{col}2", fin.revenue(i) * 10 / fin.employees[i]
        )
        ref = f"{col}6"
        value = None if ref == uncached else fin.net_debt[i] / fin.ebitda(i)
        book.formula(ws, ref, f"={col}7/'P&L'!{col}{ebitda}", value)


def _totals(rec: DocRecord, fin: Financials, rows: dict[str, int]) -> list[WorkbookTotal]:
    i = len(HISTORY) - 1
    col = YEAR_COLS[i]
    seg = [rows[f"Revenue - {n}"] for n in fin.segments]
    return [
        WorkbookTotal(
            doc=rec.file,
            metric="FY2024 total revenue (INR crore)",
            value=fin.revenue(i) / 10,
            source_range=f"P&L!{col}{seg[0]}:{col}{seg[-1]}",
            cell=f"P&L!{col}{rows['Total revenue']}",
        ),
        WorkbookTotal(
            doc=rec.file,
            metric="Total revenue FY2020-FY2024 (INR crore)",
            value=sum(fin.revenue(k) for k in range(len(HISTORY))) / 10,
            source_range=f"P&L!B{seg[0]}:{col}{seg[-1]}",
        ),
    ]


def _finish(rec: DocRecord, book: Book, out: str) -> None:
    rec.sheets = [ws.title for ws in book.wb.worksheets]
    rec.hidden_sheets = [ws.title for ws in book.wb.worksheets if ws.sheet_state != "visible"]
    rec.rows = {ws.title: ws.max_row for ws in book.wb.worksheets}
    book.save(Path(out) / rec.file)


# --- workbook 1: model with the hidden sheet and the uncached formula ------------------------


def build_model_workbook(
    out: str, file: str, company: Company, fin: Financials, injections: Injections
) -> tuple[DocRecord, list[WorkbookTotal]]:
    rec = DocRecord(file, "xlsx", f"{company.short} - Financial model", canary_for(file))
    meta = injections.take()
    book = _new_book(rec.title, meta)
    rec.plant("injection", "metadata", meta, "meta:description")
    rows = _pl_sheet(book, fin, rec)
    _segments_sheet(book, fin, rows)
    uncached = f"{YEAR_COLS[-1]}6"
    _kpi_sheet(book, fin, rows, uncached)
    rec.uncached_formulas = [f"KPIs!{uncached}"]

    ws = book.wb["P&L"]
    note = injections.take()
    ws["B2"].comment = Comment(note, "Analyst")
    rec.plant("injection", "cell_comment", note, "P&L!B2 comment")

    hidden = book.wb.create_sheet("Internal_Notes")
    hidden.sheet_state = "hidden"
    hidden["A1"] = "Reviewer notes"
    for r in (2, 3):
        text = injections.take()
        hidden[f"A{r}"] = text
        rec.plant("injection", "hidden_sheet", text, f"Internal_Notes!A{r}")
    rec.key_terms.update({"company": company.name, "currency": "INR crore"})
    _finish(rec, book, out)
    return rec, _totals(rec, fin, rows)


# --- workbook 2: budget with entities and KYC sheets ---------------------------------------------


def build_budget_workbook(
    out: str, file: str, company: Company, fin: Financials, injections: Injections
) -> tuple[DocRecord, list[WorkbookTotal]]:
    rec = DocRecord(file, "xlsx", f"{company.short} - Budget and KPIs", canary_for(file))
    book = _new_book(rec.title, "Budget workbook")
    ids = IdFactory(rng_for("wb2-ids", file))
    rows = _pl_sheet(book, fin, rec)
    _segments_sheet(book, fin, rows)
    _kpi_sheet(book, fin, rows, None)

    ent = book.wb.create_sheet("Entities")
    ent.append(["Entity", "CIN / LLPIN", "GSTIN", "ISIN", "Phone", "E-mail"])
    people = make_people(company, 3, "wb2")
    llpin = ids.llpin()
    entries = [
        (
            company.name,
            company.cin,
            company.gstin,
            company.isin or "",
            company.phone,
            people[0].email,
        ),
        (f"{company.short} Services LLP", llpin, "", "", "", people[1].email),
    ]
    for entry in entries:
        ent.append(list(entry))
    rec.plant("benign", "cin", company.cin, "Entities!B2")
    rec.plant(
        "benign", "gstin", company.gstin, "Entities!C2", note="embeds a PAN-like company PAN; keep"
    )
    if company.isin:
        rec.plant("benign", "isin", company.isin, "Entities!D2")
    rec.plant("benign", "business_phone", company.phone, "Entities!E2")
    rec.plant("benign", "business_email", people[0].email, "Entities!F2")
    rec.plant("benign", "llpin", llpin, "Entities!B3")
    rec.plant("benign", "business_email", people[1].email, "Entities!F3")

    kyc = book.wb.create_sheet("KYC")
    kyc.append(["Name", "PAN", "Aadhaar", "Bank account (with IFSC)", "Demat", "Passport"])
    note = injections.take()
    kyc["A1"].comment = Comment(note, "Compliance")
    rec.plant("injection", "cell_comment", note, "KYC!A1 comment")
    for r, person in enumerate(make_people(company, 3, "wb2-kyc"), start=2):
        pan, aadhaar = ids.pan(), ids.aadhaar()
        acct = ids.bank_account(13)
        demat = ids.demat_nsdl() if r % 2 else ids.demat_cdsl()
        passport = ids.passport()
        shown_aadhaar = spaced(aadhaar, (4, 4, 4)) if r != 3 else aadhaar
        kyc.append([person.name, pan, shown_aadhaar, f"{acct} / ICIC0001122", demat, passport])
        rec.plant("identifier", "pan", pan, f"KYC!B{r}")
        rec.plant(
            "identifier",
            "aadhaar",
            shown_aadhaar,
            f"KYC!C{r}",
            variant="spaced" if r != 3 else "contiguous",
        )
        rec.plant("identifier", "bank_account", acct, f"KYC!D{r}", variant="13-digit with IFSC")
        rec.plant("identifier", "demat_nsdl" if r % 2 else "demat_cdsl", demat, f"KYC!E{r}")
        rec.plant("identifier", "passport", passport, f"KYC!F{r}")

    payout_pan = ids.pan()
    sheet = book.wb.create_sheet(f"Payout {payout_pan}")
    sheet["A1"] = "Promoter payout schedule"
    sheet["A2"], sheet["B2"] = "FY2025E", fin.pat(len(HISTORY)) / 100
    rec.plant(
        "identifier", "pan", payout_pan, f"sheetname:Payout {payout_pan}", variant="sheet-name"
    )
    rec.key_terms.update({"company": company.name, "currency": "INR crore"})
    _finish(rec, book, out)
    return rec, _totals(rec, fin, rows)


# --- workbook 3: 50,000 transactions ----------------------------------------------------------


def build_transactions_workbook(
    out: str, file: str, company: Company, fin: Financials, injections: Injections
) -> tuple[DocRecord, list[WorkbookTotal]]:
    rec = DocRecord(file, "xlsx", f"{company.short} - FY2024 invoice register", canary_for(file))
    book = _new_book(rec.title, "Invoice register")
    rng = rng_for("wb3", file)
    ids = IdFactory(rng_for("wb3-ids", file))
    rows = _pl_sheet(book, fin, rec)
    _segments_sheet(book, fin, rows)
    _kpi_sheet(book, fin, rows, None)

    fake = faker_for("wb3-customers")
    customers = [
        f"{fake.last_name()} {s}"
        for s in ("Traders", "Retail", "Pharma", "Foods", "Industries", "Exports")
    ] * 2
    tx = book.wb.create_sheet("Transactions")
    tx.append(
        ["Date", "Invoice no.", "Customer", "Segment", "Amount (₹)", "GST (₹)", "Payment reference"]
    )
    note = injections.take()
    tx["E1"].comment = Comment(note, "Accounts")
    rec.plant("injection", "cell_comment", note, "Transactions!E1 comment")
    special = {137: "upi", 4211: "upi", 23890: "card", 41007: "card", 49999: "upi"}
    total_paise = gst_paise = 0
    start = datetime(2023, 4, 1)
    segments = list(fin.segments)
    for i in range(TRANSACTION_ROWS):
        r = i + 2
        amount = rng.randint(5_000_00, 25_00_000_00)  # paise
        gst = amount * 18 // 100
        total_paise += amount
        gst_paise += gst
        ref = f"NEFT{rng.randint(10**9, 10**10 - 1)}"
        if i in special:
            if special[i] == "upi":
                ref = ids.upi(fake.first_name().lower())
                rec.plant("identifier", "upi", ref, f"Transactions!G{r}")
            else:
                ref = TEST_CARDS[1 if i < 30000 else 8]
                rec.plant("identifier", "card", ref, f"Transactions!G{r}", variant="contiguous")
        tx.append(
            [
                start + timedelta(days=i * 366 // TRANSACTION_ROWS),
                f"INV-2024-{i + 1:06d}",
                customers[rng.randrange(len(customers))],
                segments[rng.randrange(len(segments))],
                amount / 100,
                gst / 100,
                ref,
            ]
        )
    for row in tx.iter_rows(min_row=2, min_col=5, max_col=6):
        for cell in row:
            cell.number_format = '"₹"#,##0.00'

    last = TRANSACTION_ROWS + 1
    summary = book.wb.create_sheet("Summary")
    summary["A1"], summary["B1"] = "Metric", "Value"
    summary["A2"], summary["A3"], summary["A4"] = "Total invoiced (₹)", "Total GST (₹)", "Invoices"
    book.formula(summary, "B2", f"=SUM(Transactions!E2:E{last})", total_paise / 100)
    book.formula(summary, "B3", f"=SUM(Transactions!F2:F{last})", gst_paise / 100)
    book.formula(summary, "B4", f"=COUNTA(Transactions!B2:B{last})", TRANSACTION_ROWS)
    summary["A6"] = f"Total invoiced: {fmt_inr(total_paise // 10)}"

    totals = [
        *_totals(rec, fin, rows),
        WorkbookTotal(
            doc=file,
            metric="Total invoiced (INR)",
            value=total_paise / 100,
            cell="Summary!B2",
            source_range=f"Transactions!E2:E{last}",
        ),
        WorkbookTotal(
            doc=file,
            metric="Total GST (INR)",
            value=gst_paise / 100,
            cell="Summary!B3",
            source_range=f"Transactions!F2:F{last}",
        ),
        WorkbookTotal(
            doc=file,
            metric="Invoice count",
            value=TRANSACTION_ROWS,
            cell="Summary!B4",
            method="count",
            source_range=f"Transactions!B2:B{last}",
        ),
    ]
    rec.key_terms.update({"company": company.name, "period": "FY2024", "currency": "INR"})
    _finish(rec, book, out)
    return rec, totals


# --- CSV -----------------------------------------------------------------------------------------


def build_vendor_csv(out: str, file: str, company: Company) -> DocRecord:
    rec = DocRecord(file, "csv", f"{company.short} - vendor ledger", canary_for(file))
    rng = rng_for("csv", file)
    ids = IdFactory(rng_for("csv-ids", file))
    fake = faker_for("csv-vendors")
    header = ["Vendor", "GSTIN", "Contact e-mail", "Proprietor PAN", "Amount", "Payment reference"]
    rows: list[list[str]] = []
    for i in range(40):
        r = i + 2  # CSV row number (header is row 1)
        kind = rng.choice(["Enterprises", "Agencies", "Suppliers", "Transport"])
        name = f"{fake.last_name()} {kind}"
        slug = name.split(maxsplit=1)[0].lower()
        gstin = ids.gstin(rng.choice(["27", "29", "33"]), ids.company_pan())
        email = f"accounts@{slug}-{i}.example"
        pan = ids.pan() if i % 8 == 0 else ""
        ref = ids.upi(slug) if i % 13 == 5 else f"RTGS{rng.randint(10**8, 10**9 - 1)}"
        amount = fmt_inr(rng.randint(10_000, 9_000_000))
        rows.append([name, gstin, email, pan, amount, ref])
        if i < 6:
            rec.plant("benign", "gstin", gstin, f"B{r}", note="embeds a PAN-like company PAN; keep")
            rec.plant("benign", "business_email", email, f"C{r}")
        if pan:
            rec.plant("identifier", "pan", pan, f"D{r}")
        if ref.find("@") > 0:
            rec.plant("identifier", "upi", ref, f"F{r}")
    rows.append(["Ledger reference", "", "", "", "", rec.canary])
    rec.plant("canary", "canary", rec.canary, f"F{len(rows) + 1}")
    with open(Path(out) / file, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
    rec.rows = {"csv": len(rows)}
    rec.key_terms.update({"company": company.name, "amount_format": "Indian grouping with ₹"})
    return rec
