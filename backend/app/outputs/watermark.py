"""Download watermarks (FR-024): who took the file, when (IST), from which workspace.

Applied at download time, to the decrypted file, for the downloading user:

- **XLSX:** a line in a ``Notes`` sheet (created if missing) and every sheet's footer;
- **DOCX:** every section's footer;
- **CSV:** a final ``# CONFIDENTIAL …`` line (streamed: the file is never held whole).

Outputs are values-only (ADR-030): text starting with ``= + - @`` is prefixed with
``'`` so no spreadsheet treats it as a formula, and the watermark itself (a code name
could start with ``=``) gets the same treatment. Footers escape ``&``, the header/footer
control character.
"""

import io
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Final

from docx import Document
from openpyxl import load_workbook  # type: ignore[import-untyped]

IST: Final = timezone(timedelta(hours=5, minutes=30), "IST")  # no DST
PRIVATE_SPACE_LABEL: Final = "Private space"
NOTES_SHEET: Final = "Notes"
FORMULA_PREFIXES: Final = ("=", "+", "-", "@")


@dataclass(frozen=True, slots=True)
class Watermark:
    username: str
    at: datetime
    code_name: str | None  # None for a private space

    @property
    def text(self) -> str:
        stamp = self.at.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")
        where = self.code_name or PRIVATE_SPACE_LABEL
        return f"CONFIDENTIAL - downloaded by {self.username} on {stamp} - {where}"


def values_only(text: str) -> str:
    """``text`` made safe as a spreadsheet value (formula-injection defence)."""
    return f"'{text}" if text.startswith(FORMULA_PREFIXES) else text


def _single_line(text: str) -> str:
    return " ".join(text.split())


# --- XLSX ---------------------------------------------------------------------------------


def watermark_xlsx(data: bytes, mark: Watermark) -> bytes:
    workbook = load_workbook(io.BytesIO(data))  # values as stored; nothing is evaluated
    line = values_only(_single_line(mark.text))
    footer = line.replace("&", "&&")
    for sheet in workbook.worksheets:
        for part in (sheet.oddFooter, sheet.evenFooter, sheet.firstFooter):
            part.center.text = footer
    if NOTES_SHEET in workbook.sheetnames:
        notes = workbook[NOTES_SHEET]
        row = notes.max_row + 1
    else:
        notes = workbook.create_sheet(NOTES_SHEET)
        for part in (notes.oddFooter, notes.evenFooter, notes.firstFooter):
            part.center.text = footer
        row = 1
    cell = notes.cell(row=row, column=1)
    cell.value = line
    cell.data_type = "s"  # always a string, never a formula
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


# --- DOCX ---------------------------------------------------------------------------------


def watermark_docx(data: bytes, mark: Watermark) -> bytes:
    document = Document(io.BytesIO(data))
    line = _single_line(mark.text)
    for section in document.sections:
        footers = [section.footer]
        if section.different_first_page_header_footer:
            footers.append(section.first_page_footer)
        if document.settings.odd_and_even_pages_header_footer:
            footers.append(section.even_page_footer)
        for footer in footers:
            footer.is_linked_to_previous = False
            footer.add_paragraph(line)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


# --- CSV ----------------------------------------------------------------------------------


async def watermark_csv(chunks: AsyncIterable[bytes], mark: Watermark) -> AsyncIterator[bytes]:
    """Pass the CSV through unchanged, then append the confidentiality line."""
    last = b""
    async for chunk in chunks:
        if chunk:
            last = chunk[-1:]
            yield chunk
    newline = b"\r\n" if last and last not in {b"\n", b"\r"} else b""
    line = _single_line(mark.text).replace('"', "'")
    yield newline + f"# {line}\r\n".encode()
