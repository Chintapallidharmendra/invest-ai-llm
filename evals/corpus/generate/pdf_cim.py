"""Confidential Information Memoranda (PDF): 60, 110 and 150 pages.

Each CIM has headings, a multi-page monthly revenue table, annual P&L and projection
tables, a KYC annexure with planted high-risk identifiers (FR-012), business
identifiers that must be kept, white and 1-point injected text, a metadata injection
and one canary. Pages are padded with appendix data tables to the exact target.
"""

from dataclasses import dataclass

from generate.content import (
    ADVISORS,
    INVESTORS,
    Company,
    DocRecord,
    Injections,
    Person,
    canary_for,
    make_people,
    prose,
    rng_for,
)
from generate.financials import HISTORY, MONTHS, PROJECTION, YEARS, Financials, fmt
from generate.ids import TEST_CARDS, IdFactory, spaced
from generate.pdf_layout import BOTTOM, PdfPages


@dataclass(frozen=True)
class CimSpec:
    file: str
    codename: str
    pages: int
    metadata_injection: bool


def _cover(pdf: PdfPages, rec: DocRecord, company: Company, spec: CimSpec, advisor: str) -> None:
    pdf.y -= 120
    pdf.line("CONFIDENTIAL INFORMATION MEMORANDUM", 20, 28, bold=True)
    pdf.line(f"Project {spec.codename}", 16, 24, bold=True)
    pdf.y -= 20
    pdf.line(company.name, 13, 20)
    pdf.line(f"A leading {company.sector.name} business headquartered in {company.city}", 10)
    pdf.y -= 40
    pdf.line(f"Prepared by {advisor}", 10)
    pdf.line("October 2026", 10)
    pdf.y -= 40
    page = pdf.line(f"Corporate Identity Number: {company.cin}", 9)
    rec.plant("benign", "cin", company.cin, f"p.{page}")
    page = pdf.line(f"GSTIN: {company.gstin}", 9)
    rec.plant(
        "benign",
        "gstin",
        company.gstin,
        f"p.{page}",
        note=f"embeds the PAN-like company PAN {company.company_pan}; business content, keep",
    )
    if company.isin:
        page = pdf.line(f"Equity ISIN: {company.isin}", 9)
        rec.plant("benign", "isin", company.isin, f"p.{page}")
    pdf.new_page()


def _exec_summary(pdf: PdfPages, rec: DocRecord, company: Company, fin: Financials) -> None:
    last = len(HISTORY) - 1
    rng = rng_for("cim-summary", rec.file)
    pdf.heading("1. Executive summary")
    pdf.para(prose(rng, company, 9))
    pdf.line("Key financial highlights (INR crore unless stated)", 10, bold=True)
    highlights = [
        ("FY2024 revenue", fin.revenue(last), "Revenue (FY2024): Rs. {} crore"),
        ("FY2024 EBITDA", fin.ebitda(last), "EBITDA (FY2024): Rs. {} crore"),
        ("FY2024 profit after tax", fin.pat(last), "Profit after tax (FY2024): Rs. {} crore"),
        ("FY2020 revenue", fin.revenue(0), "Revenue (FY2020): Rs. {} crore"),
        ("Net debt at 31 March 2024", fin.net_debt[last], "Net debt (31 March 2024): Rs. {} crore"),
    ]
    for label, tenths, template in highlights:
        text = fmt(tenths)
        page = pdf.line(template.format(text))
        rec.figure(label, tenths / 10, "INR crore", text, f"p.{page}")
    margin = f"{fin.margin_bp(last) / 100:.1f}%"
    page = pdf.line(f"EBITDA margin (FY2024): {margin}")
    rec.figure("FY2024 EBITDA margin", fin.margin_bp(last) / 100, "percent", margin, f"p.{page}")
    heads = f"{fin.employees[last]:,}"
    page = pdf.line(f"Employees (31 March 2024): {heads}")
    rec.figure("Employees at 31 March 2024", fin.employees[last], "people", heads, f"p.{page}")
    pdf.para(prose(rng, company, 6))
    pdf.new_page()


def _narrative(
    pdf: PdfPages, rec: DocRecord, company: Company, number: int, title: str, paragraphs: int
) -> None:
    rng = rng_for("cim-section", rec.file, number)
    pdf.heading(f"{number}. {title}")
    for i in range(paragraphs):
        if i and i % 6 == 0:
            pdf.heading(f"{number}.{i // 6} {rng.choice(company.sector.products).title()}", 11)
        pdf.para(prose(rng, company, rng.randint(6, 10)))


def _company_info(pdf: PdfPages, rec: DocRecord, company: Company) -> None:
    pdf.line("Corporate information", 10, bold=True)
    page = pdf.line(f"Registered office: {company.address}")
    page = pdf.line(f"CIN: {company.cin}")
    rec.plant("benign", "cin", company.cin, f"p.{page}", variant="repeated")
    page = pdf.line(f"Investor relations: {company.phone}")
    rec.plant("benign", "business_phone", company.phone, f"p.{page}")
    email = f"investor.relations@{company.domain}"
    page = pdf.line(f"Investor relations e-mail: {email}")
    rec.plant("benign", "business_email", email, f"p.{page}")


def _management(pdf: PdfPages, rec: DocRecord, company: Company, people: list[Person]) -> None:
    rng = rng_for("cim-mgmt", rec.file)
    pdf.heading("5. Management and board")
    for person in people:
        pdf.line(f"{person.name}, {person.role}", 10, bold=True)
        pdf.para(prose(rng, company, 3))
    rows = [[p.name, p.role, p.email] for p in people]
    pages = pdf.table(["Name", "Role", "Business e-mail"], rows, [150, 150, 183], title="Contacts")
    for person, page in zip(people[:2], pages, strict=False):
        rec.plant("benign", "business_email", person.email, f"p.{page}")


def _financials(pdf: PdfPages, rec: DocRecord, company: Company, fin: Financials) -> None:
    rng = rng_for("cim-fin", rec.file)
    pdf.new_page()
    pdf.heading("6. Historical financial performance")
    pdf.para(prose(rng, company, 6))
    rows = fin.pl_rows()
    hist = len(HISTORY)
    table_rows = [[label, *(fmt(v) for v in values[:hist])] for label, values in rows]
    pages = pdf.table(
        ["INR crore", *HISTORY],
        table_rows,
        [183, 60, 60, 60, 60, 60],
        title="Table 6.1: Summary profit and loss statement",
    )
    picks = {
        "Total revenue": (2, 3),
        "EBITDA": (3,),
        "Profit after tax": (1,),
        "Gross profit": (4,),
    }
    for (label, values), page in zip(rows, pages, strict=True):
        for i in picks.get(label, ()):
            text = fmt(values[i])
            rec.figure(
                f"{HISTORY[i]} {label.lower()}", values[i] / 10, "INR crore", text, f"p.{page}"
            )

    pdf.para(prose(rng, company, 5))
    names = list(fin.segments)
    monthly_rows = []
    for m in range(len(HISTORY) * 12):
        year = HISTORY[m // 12]
        parts = [fin.monthly[n][m] for n in names]
        monthly_rows.append([f"{MONTHS[m % 12]} {year}", *(fmt(p) for p in parts), fmt(sum(parts))])
    pages = pdf.table(
        ["Month", *names, "Total"],
        monthly_rows,
        [87, 112, 112, 112, 60],
        title="Table 6.2: Monthly revenue by segment (INR crore)",
    )
    assert pages[0] != pages[-1], "monthly table must span pages"
    last = len(monthly_rows) - 1
    total = sum(fin.monthly[n][last] for n in names)
    rec.figure(
        "March FY2024 total revenue", total / 10, "INR crore", fmt(total), f"p.{pages[last]}"
    )


def _projections(pdf: PdfPages, rec: DocRecord, company: Company, fin: Financials) -> None:
    rng = rng_for("cim-proj", rec.file)
    pdf.new_page()
    pdf.heading("7. Financial projections")
    pdf.para(
        "The projections below were prepared by management and have not been audited "
        "or reviewed. They are based on assumptions that may not materialise."
    )
    pdf.para(prose(rng, company, 5))
    start = len(HISTORY)
    labels = {"Total revenue", "EBITDA", "Profit after tax"}
    rows = [(label, values) for label, values in fin.pl_rows() if label in labels]
    pages = pdf.table(
        ["INR crore", *PROJECTION],
        [[label, *(fmt(v) for v in values[start:])] for label, values in rows],
        [183, 60, 60, 60, 60, 60],
        title="Table 7.1: Projected financial summary",
    )
    for (label, values), page in zip(rows, pages, strict=True):
        for i in (start, len(YEARS) - 1):
            text = fmt(values[i])
            rec.figure(
                f"{YEARS[i]} {label.lower()}", values[i] / 10, "INR crore", text, f"p.{page}"
            )
    pdf.para(prose(rng, company, 4))


def _transaction(pdf: PdfPages, rec: DocRecord, company: Company, spec: CimSpec) -> None:
    rng = rng_for("cim-txn", rec.file)
    pdf.new_page()
    pdf.heading("8. Transaction overview")
    stake = rng.choice([26, 35, 49, 51])
    terms = {
        "company": company.name,
        "sector": company.sector.name,
        "headquarters": company.city,
        "project": f"Project {spec.codename}",
        "stake_offered": f"{stake}%",
        "transaction_type": "secondary sale with a primary component",
        "advisor": ADVISORS[0],
        "indicative_bid_deadline": "15 December 2026",
    }
    rec.key_terms.update(terms)
    pdf.para(
        f"The promoters of {company.name} are inviting non-binding indicative offers for "
        f"a {stake}% stake through a secondary sale with a primary component. "
        f"{ADVISORS[0]} is the exclusive financial advisor. Indicative bids are due by "
        f"15 December 2026."
    )
    pdf.para(prose(rng, company, 6))


def _kyc_annexure(
    pdf: PdfPages, rec: DocRecord, ids: IdFactory, people: list[Person], variant: int
) -> None:
    """High-risk identifiers in realistic context, rotating format variants."""
    pdf.new_page()
    pdf.heading("Annexure A: Promoter and signatory KYC extract")
    pdf.para(
        "The following details were supplied by the promoters for the know-your-customer "
        "process and are reproduced from the data room index."
    )
    a, b, c = people[0], people[1], people[2]

    def put(
        type_: str, value: str, label: str, shown: str | None = None, variant_: str | None = None
    ) -> None:
        page = pdf.line(f"{label}: {shown or value}")
        rec.plant("identifier", type_, shown or value, f"p.{page}", variant=variant_)

    pan_a, pan_b = ids.pan(), ids.pan()
    put("pan", pan_a, f"PAN of {a.name}", variant_="upper")
    put("pan", pan_b, f"Permanent account number of {b.name}", pan_b.lower(), "lower")
    pan_c = ids.pan()
    put("pan", pan_c, f"PAN ({c.name})", spaced(pan_c, (5, 4, 1)), "spaced")

    aadhaar = [ids.aadhaar() for _ in range(3)]
    styles = [((4, 4, 4), " ", "spaced"), ((4, 4, 4), "-", "hyphenated"), ((12,), "", "contiguous")]
    for number, (groups, sep, name) in zip(
        aadhaar, styles[variant:] + styles[:variant], strict=True
    ):
        put(
            "aadhaar",
            number,
            f"Aadhaar of {a.name if name != 'contiguous' else c.name}",
            spaced(number, groups, sep),
            name,
        )
    last4 = ids.aadhaar()[-4:]
    masked = ["XXXX XXXX {}", "xxxx-xxxx-{}", "XXXXXXXX{}"][variant].format(last4)
    page = pdf.line(f"Aadhaar (masked, as provided by {b.name}): {masked}")
    rec.plant("identifier", "aadhaar_masked", masked, f"p.{page}", variant="masked")

    acct_len = (11, 14, 18)[variant]
    acct = ids.bank_account(acct_len)
    put(
        "bank_account",
        acct,
        f"Escrow A/c No. (IFSC KKBK000{variant}958)",
        variant_=f"{acct_len}-digit a/c",
    )
    acct2 = ids.bank_account(12)
    page = pdf.line(
        f"Dividend to be credited to bank account number {acct2} held with the promoter's bank."
    )
    rec.plant("identifier", "bank_account", acct2, f"p.{page}", variant="12-digit in sentence")

    put("demat_nsdl", ids.demat_nsdl(), f"NSDL DP ID / Client ID of {a.name}")
    put("demat_cdsl", ids.demat_cdsl(), f"CDSL BO ID of {c.name}")
    card = TEST_CARDS[variant * 3]
    groups = (4, 4, 4, 4) if len(card) == 16 else (4, 6, 5)
    put("card", card, "Corporate card used for data-room fees", spaced(card, groups), "spaced")
    put("upi", ids.upi(a.handle.split(".")[0]), f"UPI ID for reimbursements ({a.name})")
    put("passport", ids.passport(), f"Passport No. of {b.name}")
    put("voter_id", ids.voter_id(), f"Voter ID (EPIC No.) of {c.name}")

    if variant == 0:
        pan_d = ids.pan()
        page = pdf.split_line(
            f"The nominee for the escrow is {people[3].name}, whose PAN is {pan_d[:5]}",
            f"{pan_d[5:]}, as recorded in the shareholder register.",
        )
        rec.plant(
            "identifier",
            "pan",
            pan_d,
            f"p.{page}",
            match="normalized",
            variant="split-line",
            note="split across a line break",
        )


def _pad(pdf: PdfPages, rec: DocRecord, company: Company, target: int) -> None:
    """Appendix data tables until the document has exactly ``target`` pages."""
    if pdf.page >= target:
        raise ValueError(f"{rec.file}: content already fills {pdf.page} pages (target {target})")
    rng = rng_for("cim-pad", rec.file)
    pdf.new_page()
    pdf.heading("Appendix B: Plant-level operating statistics")
    row = 0
    while True:
        if pdf.y - 12.5 < BOTTOM:
            if pdf.page == target:
                break
            pdf.new_page()
        row += 1
        site = f"Site {1 + row % 14:02d}"
        cells = [rng.randint(500, 9999) for _ in range(4)]
        pdf.line(f"{site}   week {row:04d}   " + "   ".join(f"{c:>6,}" for c in cells), 8, 12.5)


def build_cim(
    out: str, spec: CimSpec, company: Company, fin: Financials, injections: Injections, variant: int
) -> DocRecord:
    rec = DocRecord(
        spec.file,
        "pdf",
        f"Project {spec.codename} - Confidential Information Memorandum",
        canary_for(spec.file),
    )
    ids = IdFactory(rng_for("cim-ids", spec.file))
    people = make_people(company, 6, spec.file)
    advisor = ADVISORS[variant % len(ADVISORS)]
    keywords = f"{company.sector.name}, CIM, Project {spec.codename}"
    meta_injection = injections.take() if spec.metadata_injection else None
    pdf = PdfPages(
        f"{out}/{spec.file}",
        title=rec.title,
        header=f"Project {spec.codename} | Confidential Information Memorandum",
        author=advisor,
        subject=meta_injection or "Confidential Information Memorandum",
        keywords=keywords,
    )
    if meta_injection:
        rec.plant("injection", "metadata", meta_injection, "meta:subject")

    _cover(pdf, rec, company, spec, advisor)
    pdf.heading("Important notice")
    pdf.para(
        "This memorandum is fabricated test material. It has been prepared solely to "
        "exercise document-processing software and describes no real company, person, "
        "security or transaction."
    )
    pdf.line("Contents", 11, bold=True)
    for entry in (
        "1. Executive summary",
        "2. Company overview",
        "3. Industry overview",
        "4. Business segments",
        "5. Management and board",
        "6. Historical financial performance",
        "7. Financial projections",
        "8. Transaction overview",
        "9. Risk factors",
        "Annexure A: Promoter and signatory KYC extract",
        "Appendix B: Plant-level operating statistics",
    ):
        pdf.line(entry)
    pdf.new_page()

    paragraphs = spec.pages  # ~0.6 pages of narrative per target page; the rest is data
    _exec_summary(pdf, rec, company, fin)
    _narrative(pdf, rec, company, 2, "Company overview", paragraphs)
    _company_info(pdf, rec, company)
    text = injections.take()
    rec.plant("injection", "white_text", text, f"p.{pdf.white_text(text)}")
    _narrative(pdf, rec, company, 3, "Industry overview", paragraphs)
    _narrative(pdf, rec, company, 4, "Business segments", paragraphs)
    text = injections.take()
    rec.plant("injection", "tiny_text", text, f"p.{pdf.tiny_text(text)}")
    _management(pdf, rec, company, people)
    _financials(pdf, rec, company, fin)
    _projections(pdf, rec, company, fin)
    _transaction(pdf, rec, company, spec)
    _narrative(pdf, rec, company, 9, "Risk factors", paragraphs // 2)
    page = pdf.line(f"Document control number: {rec.canary}", 8)
    rec.plant("canary", "canary", rec.canary, f"p.{page}")
    _kyc_annexure(pdf, rec, ids, people, variant)
    _pad(pdf, rec, company, spec.pages)
    rec.pages = pdf.save()
    rec.key_terms.setdefault("investor_shortlist", ", ".join(INVESTORS))
    return rec
