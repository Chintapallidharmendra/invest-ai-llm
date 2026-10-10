"""Dispatch a file to its parser. Runs inside the sandboxed child (``app.sandbox``).

Imported by the forkserver at start-up; Docling is imported lazily, in the child.
"""

from app.csv_parse import parse_csv
from app.docling_parse import parse_document
from app.schema import ErrorCode, FileKind, Limits, ParseError, ParseResult
from app.xlsx_parse import parse_xlsx


def parse(kind: FileKind, data: bytes, limits: Limits) -> ParseResult:
    if len(data) > limits.max_bytes:
        raise ParseError(ErrorCode.LIMIT_EXCEEDED, "bytes")
    if not data:
        raise ParseError(ErrorCode.CORRUPT_FILE, "empty")
    if kind is FileKind.XLSX:
        return parse_xlsx(data, limits)
    if kind is FileKind.CSV:
        return parse_csv(data, limits)
    return parse_document(kind, data, limits)
