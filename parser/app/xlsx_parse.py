"""XLSX via openpyxl: cached values only, nothing evaluated, no external links (ADR-027).

Pass 1 (``read_only=True, data_only=True, keep_links=False``) streams every sheet's
cached values. Pass 2 (``data_only=False``) streams again to count formula cells that
have no cached value: the file was never recalculated, so their value is unknown.
Comments aren't available in read-only mode, so they are read straight from each
sheet's comments part in the package.
"""

import io
import posixpath
import zipfile
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from typing import Any, Final
from xml.etree import ElementTree as ET

from openpyxl import load_workbook  # type: ignore[import-untyped]
from openpyxl.utils import get_column_letter  # type: ignore[import-untyped]

from app.schema import (
    CellComment,
    CellValue,
    ErrorCode,
    FileKind,
    Limits,
    ParseError,
    ParseResult,
    Sheet,
    SheetRow,
)

_NS: Final = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
_COMMENTS_REL: Final = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
)
# A comments part above this is refused rather than parsed (zip-bomb defence).
_MAX_PART_BYTES: Final = 64 * 1024 * 1024


def _value(value: Any) -> CellValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    return str(value)  # e.g. an error value such as "#DIV/0!"


def _trim(values: list[CellValue]) -> list[CellValue]:
    while values and values[-1] is None:
        values.pop()
    return values


def _open(data: bytes, *, data_only: bool) -> Any:
    try:
        return load_workbook(
            io.BytesIO(data), read_only=True, data_only=data_only, keep_links=False
        )
    except (zipfile.BadZipFile, KeyError, ValueError, TypeError, OSError, ET.ParseError):
        raise ParseError(ErrorCode.CORRUPT_FILE) from None


def _rows(sheet: Any, limits: Limits) -> Iterator[SheetRow]:
    for index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
        if index > limits.max_rows_per_sheet:
            raise ParseError(ErrorCode.LIMIT_EXCEEDED, "rows")
        values = _trim([_value(v) for v in row])
        if values:
            yield SheetRow(row=index, values=values)


def _uncached_formulas(data: bytes, cached: dict[str, set[str]]) -> dict[str, int]:
    """Per sheet: formula cells whose cached value (from pass 1) is empty."""
    workbook = _open(data, data_only=False)
    try:
        counts: dict[str, int] = {}
        for sheet in workbook.worksheets:
            have = cached.get(sheet.title, set())
            count = 0
            for row in sheet.iter_rows():
                for cell in row:
                    if cell.data_type == "f" and cell.coordinate not in have:
                        count += 1
            counts[sheet.title] = count
        return counts
    finally:
        workbook.close()


def _resolve(folder: str, target: str) -> str:
    """A relationship target as a package part name (targets may be absolute)."""
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(folder, target))


def _comments(data: bytes) -> dict[str, list[CellComment]]:
    """Comments per sheet name, read from the package's comments parts."""
    with zipfile.ZipFile(io.BytesIO(data)) as package:

        def xml(name: str) -> ET.Element | None:
            try:
                info = package.getinfo(name)
            except KeyError:
                return None
            if info.file_size > _MAX_PART_BYTES:
                raise ParseError(ErrorCode.LIMIT_EXCEEDED, "part_size")
            return ET.fromstring(package.read(info))  # noqa: S314 (no entity expansion in expat defaults)

        workbook, rels = xml("xl/workbook.xml"), xml("xl/_rels/workbook.xml.rels")
        if workbook is None or rels is None:
            return {}
        targets = {r.get("Id"): r.get("Target", "") for r in rels.findall("rel:Relationship", _NS)}
        result: dict[str, list[CellComment]] = {}
        for sheet in workbook.iterfind("main:sheets/main:sheet", _NS):
            sheet_path = _resolve("xl", targets.get(sheet.get(f"{{{_NS['r']}}}id"), ""))
            folder, name = posixpath.split(sheet_path)
            sheet_rels = xml(f"{folder}/_rels/{name}.rels")
            if sheet_rels is None:
                continue
            for rel in sheet_rels.findall("rel:Relationship", _NS):
                if rel.get("Type") != _COMMENTS_REL:
                    continue
                part = _resolve(folder, rel.get("Target", ""))
                comments = xml(part)
                if comments is None:
                    continue
                authors = [a.text or "" for a in comments.iterfind("main:authors/main:author", _NS)]
                for comment in comments.iterfind("main:commentList/main:comment", _NS):
                    text = "".join(t.text or "" for t in comment.iter(f"{{{_NS['main']}}}t"))
                    author_id = comment.get("authorId")
                    author = (
                        authors[int(author_id)]
                        if author_id is not None
                        and author_id.isdigit()
                        and int(author_id) < len(authors)
                        else None
                    )
                    result.setdefault(sheet.get("name", ""), []).append(
                        CellComment(ref=comment.get("ref", ""), text=text, author=author or None)
                    )
        return result


def parse_xlsx(data: bytes, limits: Limits) -> ParseResult:
    workbook = _open(data, data_only=True)
    sheets: list[tuple[str, bool, list[SheetRow], set[str]]] = []
    try:
        if len(workbook.worksheets) > limits.max_sheets:
            raise ParseError(ErrorCode.LIMIT_EXCEEDED, "sheets")
        for sheet in workbook.worksheets:
            rows = list(_rows(sheet, limits))
            cached = {
                f"{get_column_letter(col)}{r.row}"
                for r in rows
                for col, value in enumerate(r.values, start=1)
                if value is not None
            }
            sheets.append((sheet.title, sheet.sheet_state != "visible", rows, cached))
    finally:
        workbook.close()
    uncached = _uncached_formulas(data, {name: cached for name, _, _, cached in sheets})
    comments = _comments(data)
    return ParseResult(
        kind=FileKind.XLSX,
        sheets=[
            Sheet(
                name=name,
                hidden=hidden,
                rows=rows,
                comments=comments.get(name, []),
                formulas_without_cached_value=uncached.get(name, 0),
            )
            for name, hidden, rows, _ in sheets
        ],
    )
