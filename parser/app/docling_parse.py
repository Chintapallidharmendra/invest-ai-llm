"""PDF, DOCX and PPTX via Docling, with locators (ADR-027).

Docling gives the text in reading order, headings and table structure (TableFormer for
PDFs, from the offline models at ``DOCLING_ARTIFACTS_PATH``; no OCR here, that is 9.3).
The libraries Docling itself is built on fill in what it doesn't report:

- **pypdfium2:** page count (checked before conversion), pages without a text layer
  (``needs_ocr``) and document metadata;
- **python-docx:** ``para N`` locators (Docling has no page for DOCX), review comments,
  core properties and the page count (rendered or explicit page breaks);
- **python-pptx:** slide count (checked first), speaker notes and core properties.

Locators follow the corpus convention (``evals/corpus/generate/manifest_schema.py``):
``p.N``, ``slide N``, ``slide N notes``, ``para N``, ``table T``, ``comment N``,
``meta:<property>``.
"""

import io
import os
import re
import zipfile
from functools import cache
from pathlib import Path
from typing import Any, Final

from app.schema import (
    ErrorCode,
    FileKind,
    Limits,
    ParseError,
    ParseResult,
    Table,
    TableCell,
    TextItem,
    TextKind,
)

OLE_MAGIC: Final = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy .doc/.ppt, or encrypted OOXML
_HEADING_LABELS: Final = {"title", "section_header"}
_SKIP_LABELS: Final = {"page_header", "page_footer"}
_SPACE: Final = re.compile(r"\s+")
_DOCX_PAGE_BREAK: Final = re.compile(rb'<w:br [^>]*w:type="page"|<w:lastRenderedPageBreak')
_APP_PAGES: Final = re.compile(rb"<Pages>(\d+)</Pages>")
_META_FIELDS: Final = ("title", "subject", "author", "keywords", "comments", "category")


def _norm(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def artifacts_path() -> Path | None:
    value = os.environ.get("DOCLING_ARTIFACTS_PATH")
    return Path(value) if value else None


@cache
def _converter() -> Any:
    """One converter per process (it loads the layout and table models on first use)."""
    from docling.datamodel.base_models import InputFormat  # noqa: PLC0415
    from docling.datamodel.pipeline_options import PdfPipelineOptions  # noqa: PLC0415
    from docling.document_converter import DocumentConverter, PdfFormatOption  # noqa: PLC0415

    options = PdfPipelineOptions(
        artifacts_path=artifacts_path(),
        do_ocr=False,  # 9.3
        do_table_structure=True,
        generate_page_images=False,
        generate_picture_images=False,
    )
    return DocumentConverter(
        allowed_formats=[InputFormat.PDF, InputFormat.DOCX, InputFormat.PPTX],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
    )


def _convert(kind: FileKind, data: bytes) -> Any:
    from docling.datamodel.base_models import ConversionStatus, DocumentStream  # noqa: PLC0415

    try:
        result = _converter().convert(
            DocumentStream(name=f"upload.{kind.value}", stream=io.BytesIO(data)),
            raises_on_error=False,
        )
    except MemoryError:
        raise
    except Exception:
        raise ParseError(ErrorCode.CORRUPT_FILE) from None
    if result.status not in {ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS}:
        raise ParseError(ErrorCode.CORRUPT_FILE)
    return result.document


# --- Per-format checks and extras ---------------------------------------------------------


def _pdf_info(data: bytes, limits: Limits) -> tuple[int, list[str], list[TextItem]]:
    import pypdfium2  # type: ignore[import-untyped]  # noqa: PLC0415

    try:
        pdf = pypdfium2.PdfDocument(data)
    except pypdfium2.PdfiumError:
        raise ParseError(ErrorCode.CORRUPT_FILE) from None
    try:
        pages = len(pdf)
        if pages > limits.max_pages:
            raise ParseError(ErrorCode.LIMIT_EXCEEDED, "pages")
        needs_ocr = []
        for index in range(pages):
            page = pdf[index]
            textpage = page.get_textpage()
            if textpage.count_chars() == 0:
                needs_ocr.append(f"p.{index + 1}")
            textpage.close()
            page.close()
        metadata = pdf.get_metadata_dict(skip_empty=True)
    finally:
        pdf.close()
    meta = [
        (f"meta:{key.lower()}", value)
        for key, value in metadata.items()
        if key.lower() in _META_FIELDS and isinstance(value, str) and value.strip()
    ]
    return pages, needs_ocr, _meta_items(meta)


def _meta_items(pairs: list[tuple[str, str]]) -> list[TextItem]:
    return [TextItem(order=0, kind=TextKind.METADATA, locator=loc, text=t) for loc, t in pairs]


def _core_properties(props: Any) -> list[TextItem]:
    pairs = []
    for field in _META_FIELDS:
        value = getattr(props, field, None)
        if isinstance(value, str) and value.strip():
            pairs.append((f"meta:{field}", value))
    return _meta_items(pairs)


def _ooxml(data: bytes) -> zipfile.ZipFile:
    if data.startswith(OLE_MAGIC):
        raise ParseError(ErrorCode.UNSUPPORTED, "legacy_or_encrypted")
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ParseError(ErrorCode.CORRUPT_FILE) from None


def _docx_pages(data: bytes) -> int | None:
    with _ooxml(data) as package:
        names = set(package.namelist())
        if "word/document.xml" not in names:
            raise ParseError(ErrorCode.CORRUPT_FILE)
        if package.getinfo("word/document.xml").file_size > 256 * 1024 * 1024:
            raise ParseError(ErrorCode.LIMIT_EXCEEDED, "part_size")
        body = package.read("word/document.xml")
        recorded = None
        if "docProps/app.xml" in names:
            match = _APP_PAGES.search(package.read("docProps/app.xml"))
            recorded = int(match.group(1)) if match else None
    breaks = len(_DOCX_PAGE_BREAK.findall(body))
    counted = breaks + 1 if breaks else None
    candidates = [n for n in (recorded, counted) if n]
    return max(candidates) if candidates else None


def _docx_extras(data: bytes) -> tuple[list[str], list[TextItem], list[TextItem]]:
    """Body paragraph texts (for ``para N``), comments and metadata."""
    import docx  # noqa: PLC0415

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception:
        raise ParseError(ErrorCode.CORRUPT_FILE) from None
    paragraphs = [_norm(p.text) for p in document.paragraphs]
    comments = [
        TextItem(order=0, kind=TextKind.COMMENT, locator=f"comment {n}", text=c.text)
        for n, c in enumerate(document.comments, start=1)
        if c.text.strip()
    ]
    return paragraphs, comments, _core_properties(document.core_properties)


def _pptx_extras(data: bytes, limits: Limits) -> tuple[int, list[TextItem], list[TextItem]]:
    import pptx  # noqa: PLC0415

    _ooxml(data).close()
    try:
        presentation = pptx.Presentation(io.BytesIO(data))
    except Exception:
        raise ParseError(ErrorCode.CORRUPT_FILE) from None
    slides = len(presentation.slides)
    if slides > limits.max_slides:
        raise ParseError(ErrorCode.LIMIT_EXCEEDED, "slides")
    notes = []
    for number, slide in enumerate(presentation.slides, start=1):
        if slide.has_notes_slide:
            text = (
                slide.notes_slide.notes_text_frame.text
                if slide.notes_slide.notes_text_frame
                else ""
            )
            if text.strip():
                notes.append(
                    TextItem(
                        order=0, kind=TextKind.NOTE, locator=f"slide {number} notes", text=text
                    )
                )
    return slides, notes, _core_properties(presentation.core_properties)


# --- Docling items --------------------------------------------------------------------------


class _ParaLocator:
    """Finds each Docling text item's DOCX body paragraph, walking forward in order."""

    def __init__(self, paragraphs: list[str]) -> None:
        self.paragraphs = paragraphs
        self.next = 0
        self.last = 1

    def locate(self, text: str) -> str:
        wanted = _norm(text)
        for index in range(self.next, len(self.paragraphs)):
            if self.paragraphs[index] == wanted:
                self.next, self.last = index + 1, index + 1
                return f"para {index + 1}"
        return f"para {self.last}"  # e.g. text in a text box: near the last match


def _page_locator(kind: FileKind, item: Any) -> str | None:
    prov = getattr(item, "prov", None) or []
    if not prov:
        return None
    page = prov[0].page_no
    return f"slide {page}" if kind is FileKind.PPTX else f"p.{page}"


def _table(order: int, item: Any, document: Any, locator: str) -> Table:
    data = item.data
    cells = [
        TableCell(
            row=c.start_row_offset_idx,
            col=c.start_col_offset_idx,
            row_span=max(1, c.end_row_offset_idx - c.start_row_offset_idx),
            col_span=max(1, c.end_col_offset_idx - c.start_col_offset_idx),
            text=c.text,
            header=bool(c.column_header or c.row_header),
        )
        for c in data.table_cells
    ]
    caption = item.caption_text(document) or None
    return Table(
        order=order,
        locator=locator,
        caption=caption,
        n_rows=data.num_rows,
        n_cols=data.num_cols,
        cells=cells,
    )


def _items(
    kind: FileKind, document: Any, paragraphs: list[str] | None
) -> tuple[list[TextItem], list[Table]]:
    from docling_core.types.doc.items.table.table import TableItem  # noqa: PLC0415
    from docling_core.types.doc.items.text import TextItem as DoclingText  # noqa: PLC0415

    para = _ParaLocator(paragraphs) if paragraphs is not None else None
    texts: list[TextItem] = []
    tables: list[Table] = []
    order = 0
    for item, _level in document.iterate_items():
        if isinstance(item, TableItem):
            locator = _page_locator(kind, item) or f"table {len(tables) + 1}"
            tables.append(_table(order, item, document, locator))
            order += 1
            continue
        if not isinstance(item, DoclingText) or not item.text.strip():
            continue
        label = str(getattr(item.label, "value", item.label))
        if label in _SKIP_LABELS:
            continue
        locator = para.locate(item.text) if para is not None else _page_locator(kind, item) or "p.1"
        text_kind = TextKind.HEADING if label in _HEADING_LABELS else TextKind.TEXT
        texts.append(TextItem(order=order, kind=text_kind, locator=locator, text=item.text))
        order += 1
    return texts, tables


def _numbered(items: list[TextItem], start: int) -> list[TextItem]:
    return [i.model_copy(update={"order": start + n}) for n, i in enumerate(items)]


def parse_document(kind: FileKind, data: bytes, limits: Limits) -> ParseResult:
    pages: int | None = None
    slides: int | None = None
    needs_ocr: list[str] = []
    extra: list[TextItem] = []
    paragraphs: list[str] | None = None
    if kind is FileKind.PDF:
        if not data.startswith(b"%PDF-"):
            raise ParseError(ErrorCode.CORRUPT_FILE)
        pages, needs_ocr, extra = _pdf_info(data, limits)
    elif kind is FileKind.DOCX:
        pages = _docx_pages(data)
        if pages is not None and pages > limits.max_pages:
            raise ParseError(ErrorCode.LIMIT_EXCEEDED, "pages")
        paragraphs, comments, meta = _docx_extras(data)
        extra = comments + meta
    elif kind is FileKind.PPTX:
        slides, notes, meta = _pptx_extras(data, limits)
        extra = notes + meta
    else:
        raise ParseError(ErrorCode.UNSUPPORTED)

    document = _convert(kind, data)
    texts, tables = _items(kind, document, paragraphs)
    next_order = max([t.order for t in texts] + [t.order for t in tables] + [-1]) + 1
    return ParseResult(
        kind=kind,
        pages=pages,
        slides=slides,
        text=texts + _numbered(extra, next_order),
        tables=tables,
        needs_ocr=needs_ocr,
    )
