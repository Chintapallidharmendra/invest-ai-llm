"""CSV via Polars, with encoding sniffing: UTF-8 (with or without BOM), UTF-16 (BOM)
or cp1252 (anything that isn't valid UTF-8). Every value stays text, exactly as written:
"1,579.4" or "007" must not be reinterpreted here.
"""

import codecs
import io
from typing import Final, Literal

import polars as pl

from app.schema import CsvTable, ErrorCode, FileKind, Limits, ParseError, ParseResult

type Encoding = Literal["utf-8", "utf-16", "cp1252"]

_UTF16_BOMS: Final = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)


def sniff(data: bytes) -> tuple[Encoding, str]:
    """The encoding and the decoded text."""
    if data.startswith(_UTF16_BOMS):
        try:
            return "utf-16", data.decode("utf-16")
        except UnicodeDecodeError:
            raise ParseError(ErrorCode.CORRUPT_FILE, "encoding") from None
    try:
        return "utf-8", data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return "cp1252", data.decode("cp1252")
    except UnicodeDecodeError:  # the five bytes cp1252 leaves undefined
        raise ParseError(ErrorCode.UNSUPPORTED, "encoding") from None


def parse_csv(data: bytes, limits: Limits) -> ParseResult:
    encoding, text = sniff(data)
    if "\x00" in text:  # binary data, not text
        raise ParseError(ErrorCode.CORRUPT_FILE, "binary")
    if not text.strip():
        return ParseResult(kind=FileKind.CSV, csv=CsvTable(encoding=encoding, columns=[], rows=[]))
    try:
        frame = pl.read_csv(
            io.BytesIO(text.encode("utf-8")),
            infer_schema=False,  # every column as text
            n_rows=limits.max_rows_per_sheet + 1,
            truncate_ragged_lines=True,
        )
    except (pl.exceptions.ComputeError, pl.exceptions.NoDataError, ValueError):
        raise ParseError(ErrorCode.CORRUPT_FILE) from None
    if frame.height > limits.max_rows_per_sheet:
        raise ParseError(ErrorCode.LIMIT_EXCEEDED, "rows")
    return ParseResult(
        kind=FileKind.CSV,
        csv=CsvTable(
            encoding=encoding, columns=frame.columns, rows=[list(r) for r in frame.rows()]
        ),
    )
