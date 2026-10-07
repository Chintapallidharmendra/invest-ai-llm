# Synthetic confidential corpus

Fabricated deal-style documents for tests, evals and the UAT environment (Story 1.7;
ADR-037). **Real deal documents must never be added** to this directory, to `build/`,
to tests, to evals or to the UAT VM. Every person, company, deal, figure and identifier
here is generated from a fixed seed (Faker `en_IN` plus hand-written templates). Every
page, slide and sheet carries a "SYNTHETIC TEST DOCUMENT" banner.

## Usage

```sh
make corpus                                       # build/ + manifest.yaml + self-check
make -f evals/corpus/Makefile.inc corpus-verify   # CI: fail if manifest.yaml would change
make -f evals/corpus/Makefile.inc corpus-test     # acceptance tests (evals/corpus/tests)
```

`build/` is git-ignored. `manifest.yaml` is generated and **committed**. It records
the SHA-256 of every output, so an unexpected change in a rebuild shows up as a diff.
Outputs are byte-stable for the pinned dependency versions in `backend/uv.lock`:
reportlab `invariant=1`, fixed OOXML timestamps and zip entry dates, seeded RNGs.

## Contents

| File | Format | What it exercises |
|---|---|---|
| `cim_project_alpha_vardhan_chemicals.pdf` | PDF, 60 pp | CIM: headings, multi-page tables, projections, KYC annexure, white/1pt text, metadata injection |
| `cim_project_bodhi_sahyadri_healthcare.pdf` | PDF, 110 pp | CIM (map-reduce summaries) |
| `cim_project_chakra_nilgiri_logistics.pdf` | PDF, 150 pp | CIM (map-reduce summaries) |
| `term_sheet_v3.docx`, `term_sheet_v4.docx` | DOCX | comparison: 15 documented differences (`term_sheet_diff`); comments, white text, metadata |
| `spa_vardhan_chemicals.docx` | DOCX, 40 pp | SPA: party KYC, settlement details, comments, tiny/white text, line-broken PAN |
| `management_presentation_sahyadri.pptx` | PPTX, 25 slides | speaker notes on every slide (3 injected), ₹ table, metadata injection |
| `model_vardhan_chemicals.xlsx` | XLSX | P&L/Segments/KPIs formulas with cached values, **the one uncached formula** (`KPIs!K6`), **hidden sheet**, cell-comment injection |
| `budget_sahyadri_healthcare.xlsx` | XLSX | entity identifiers (benign), KYC sheet, a sheet name containing a PAN |
| `invoices_nilgiri_logistics.xlsx` | XLSX | **50,000-row** Transactions sheet with ₹ amounts and identifiers in payment references; Summary totals |
| `vendor_ledger_nilgiri.csv` | CSV | GSTINs (benign), proprietor PANs, UPI IDs, Indian-grouped ₹ amounts |
| `scan_kyc_form.pdf`, `scan_board_resolution.pdf` | PDF, image-only | OCR path: no text layer |

## Manifest

`generate/manifest_schema.py` defines the format. For each document it records the hash,
the structure (pages, slides, sheets, rows), a canary, key terms, figures with
locators and uncached formulas. Then come:

- `items`: every planted item with `kind`, `type`, exact `value` and `locator`, plus
  the expected handling:
  - `identifier` → `mask`: FR-012 high-risk types, including format variants (spaced,
    hyphenated, lower case, masked Aadhaar, split across a line break, in a sheet name).
  - `benign` → `keep`: CIN, LLPIN, company GSTIN (which embeds a PAN-like substring),
    ISIN, business phone and e-mail.
  - `injection` → `flag_and_exclude`: each injection text ends with a unique `[INJ-nn]`
    marker.
  - `canary` → `track`: one random token per document, for log/trace/audit and
    deletion tests.
- `term_sheet_diff`: the v3 → v4 differences (added, removed, changed).
- `workbook_totals`: sums and counts with their source ranges and cached cells.

Locators: `p.N` (PDF), `para N` / `comment N` / `meta:<property>` (DOCX), `slide N` /
`slide N notes` (PPTX), `Sheet!A1` / `Sheet!A1 comment` / `sheetname:<name>` (XLSX),
`A1` (CSV). `match: normalized` means whitespace (a line break) splits the value.
`match: ocr` means the value is only in the page image.

Identifiers are fabricated with valid check digits (Verhoeff for Aadhaar, Luhn for
cards, the GSTN scheme for GSTIN, ISIN check digits). Card numbers are the public
payment-network test numbers or use the non-issuer `60` prefix. E-mail domains use the
reserved `.example` TLD.

`check_corpus.py` re-verifies everything against `build/`. It fails if any file in
`build/` is not in the manifest.

## Notes

- PDFs embed the bundled Bitstream Vera font, which has no ₹ glyph, so PDF amounts read
  "Rs."/"INR". The rupee sign appears in the DOCX, PPTX, XLSX and CSV tables.
- To change the corpus, edit the generators, run `make corpus`, and commit the
  generator change together with the regenerated `manifest.yaml`.
