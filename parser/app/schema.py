"""The parser's request limits and result schema, shared with the worker (ADR-027).

**Versioned:** ``schema_version`` changes whenever a field is removed or changes meaning;
adding an optional field doesn't change it. The worker (9.4) rejects versions it doesn't
know.

Every extracted fact keeps a ``locator``: ``p.N`` (PDF/DOCX page), ``slide N`` (PPTX),
``Sheet!A1`` (XLSX cell) or ``row N`` (CSV, 1-based data row). Text is returned as it is
in the file; nothing here is masked, so results go only to the worker, never to logs.
"""

from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION: Final = 1

type CellValue = str | int | float | bool | None


class FileKind(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    CSV = "csv"


class ErrorCode(StrEnum):
    LIMIT_EXCEEDED = "limit_exceeded"
    CORRUPT_FILE = "corrupt_file"
    TIMEOUT = "timeout"
    UNSUPPORTED = "unsupported"


class TextKind(StrEnum):
    TEXT = "text"
    HEADING = "heading"
    NOTE = "note"  # PPTX speaker notes
    COMMENT = "comment"  # DOCX/PPTX review comments
    METADATA = "metadata"  # document properties (title, author, ...)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Limits(_Model):
    """FR-042 limits; a request may lower them, never raise them (see :meth:`capped`)."""

    max_bytes: Annotated[int, Field(gt=0)] = 50 * 1024 * 1024
    max_pages: Annotated[int, Field(gt=0)] = 300  # PDF and DOCX
    max_slides: Annotated[int, Field(gt=0)] = 150
    max_sheets: Annotated[int, Field(gt=0)] = 50
    max_rows_per_sheet: Annotated[int, Field(gt=0)] = 500_000  # XLSX sheets and CSV
    cpu_s: Annotated[int, Field(gt=0)] = 120
    wall_s: Annotated[float, Field(gt=0)] = 180.0
    address_space_bytes: Annotated[int, Field(gt=0)] = 3 * 1024**3

    def capped(self, requested: "Limits | None") -> "Limits":
        """The stricter of ``self`` (the service's) and the request's, per field."""
        if requested is None:
            return self
        return Limits(
            **{
                name: min(getattr(self, name), getattr(requested, name))
                for name in Limits.model_fields
            }
        )


# --- Documents (PDF, DOCX, PPTX) ----------------------------------------------------------


class TextItem(_Model):
    order: int  # reading order across the whole document, from 0
    kind: TextKind
    locator: str
    text: str


class TableCell(_Model):
    row: int  # 0-based
    col: int
    row_span: int = 1
    col_span: int = 1
    text: str
    header: bool = False


class Table(_Model):
    order: int  # position in the reading order, like TextItem.order
    locator: str
    caption: str | None
    n_rows: int
    n_cols: int
    cells: list[TableCell]


# --- Sheets (XLSX, CSV) ---------------------------------------------------------------------


class SheetRow(_Model):
    row: int  # 1-based sheet row; empty rows are omitted
    values: list[CellValue]  # from column A; trailing empty cells trimmed


class CellComment(_Model):
    ref: str  # e.g. "C4"
    text: str
    author: str | None = None


class Sheet(_Model):
    name: str
    hidden: bool
    rows: list[SheetRow]
    comments: list[CellComment]
    # Formula cells with no cached value (never calculated): their value is unknown.
    formulas_without_cached_value: int


class CsvTable(_Model):
    encoding: Literal["utf-8", "utf-16", "cp1252"]
    columns: list[str]
    rows: list[list[str | None]]  # as text, exactly as in the file


# --- Results ------------------------------------------------------------------------------


class ParseResult(_Model):
    schema_version: Literal[1] = SCHEMA_VERSION
    ok: Literal[True] = True
    kind: FileKind
    pages: int | None = None  # PDF/DOCX (DOCX: as recorded by the authoring app, if at all)
    slides: int | None = None
    text: list[TextItem] = []
    tables: list[Table] = []
    needs_ocr: list[str] = []  # locators of pages without a text layer (OCR is 9.3)
    sheets: list[Sheet] = []
    csv: CsvTable | None = None


class ParseFailure(_Model):
    schema_version: Literal[1] = SCHEMA_VERSION
    ok: Literal[False] = False
    code: ErrorCode
    detail: str | None = None  # a fixed, content-free reason, e.g. "pages"


class ParseError(Exception):
    """Raised by the parse functions; becomes a :class:`ParseFailure`."""

    def __init__(self, code: ErrorCode, detail: str | None = None) -> None:
        super().__init__(code.value if detail is None else f"{code.value}: {detail}")
        self.code = code
        self.detail = detail

    def failure(self) -> ParseFailure:
        return ParseFailure(code=self.code, detail=self.detail)
