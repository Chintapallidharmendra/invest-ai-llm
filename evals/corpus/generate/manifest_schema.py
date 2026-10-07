"""Schema of ``evals/corpus/manifest.yaml`` (Story 1.7).

Locators:

- PDF: ``p.N`` (1-based page)
- DOCX: ``para N`` (1-based body paragraph), ``table T rRcC`` (1-based),
  ``comment N`` (1-based), ``meta:<core property>``
- PPTX: ``slide N``, ``slide N notes``, ``meta:<core property>``
- XLSX: ``Sheet!A1``, ``Sheet!A1 comment``, ``sheetname:<name>``, ``meta:<property>``
- CSV: ``A1`` (column letter + 1-based row, header is row 1)

``match`` says how a verifier finds ``value`` at the locator: ``exact`` (substring),
``normalized`` (whitespace removed on both sides, for values split across a line break)
or ``ocr`` (image-only page: the text layer must NOT contain it; OCR finds it).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ItemKind = Literal["identifier", "benign", "injection", "canary"]
Handling = Literal["mask", "keep", "flag_and_exclude", "track"]
Match = Literal["exact", "normalized", "ocr"]
DocFormat = Literal["pdf", "docx", "pptx", "xlsx", "csv"]

IdentifierType = Literal[
    "pan",
    "aadhaar",
    "aadhaar_masked",
    "bank_account",
    "demat_nsdl",
    "demat_cdsl",
    "card",
    "upi",
    "passport",
    "voter_id",
]
BenignType = Literal["cin", "llpin", "gstin", "isin", "business_phone", "business_email"]
InjectionType = Literal[
    "white_text",
    "tiny_text",
    "docx_comment",
    "speaker_note",
    "cell_comment",
    "metadata",
    "hidden_sheet",
]

HANDLING: dict[ItemKind, Handling] = {
    "identifier": "mask",
    "benign": "keep",
    "injection": "flag_and_exclude",
    "canary": "track",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlantedItem(_Strict):
    id: str
    doc: str
    kind: ItemKind
    type: str
    value: str
    locator: str
    match: Match = "exact"
    expected: Handling
    variant: str | None = None  # format variant, e.g. "spaced", "masked-x", "split-line"
    note: str | None = None


class Figure(_Strict):
    label: str
    value: float
    unit: str
    text: str  # exactly as printed
    locator: str


class DiffEntry(_Strict):
    id: str
    change: Literal["added", "removed", "changed"]
    clause: str
    v3: str | None = None  # text in v3 (None when added)
    v4: str | None = None  # text in v4 (None when removed)


class WorkbookTotal(_Strict):
    doc: str
    metric: str
    value: float
    method: Literal["sum", "count"] = "sum"
    source_range: str  # the data cells it is computed from (one rectangular range)
    cell: str | None = None  # where the workbook's own cached total lives, if any


class DocumentEntry(_Strict):
    file: str
    format: DocFormat
    title: str
    sha256: str
    bytes: int
    pages: int | None = None  # PDF pages; DOCX: explicit page breaks + 1
    slides: int | None = None
    sheets: list[str] | None = None
    hidden_sheets: list[str] = Field(default_factory=list)
    rows: dict[str, int] | None = None  # max data rows per sheet / CSV
    image_only: bool = False
    canary: str
    key_terms: dict[str, str] = Field(default_factory=dict)
    figures: list[Figure] = Field(default_factory=list)
    uncached_formulas: list[str] = Field(default_factory=list)


class Manifest(_Strict):
    schema_version: int = 1
    seed: int
    warning: str
    documents: list[DocumentEntry]
    items: list[PlantedItem]
    term_sheet_diff: list[DiffEntry]
    workbook_totals: list[WorkbookTotal]
