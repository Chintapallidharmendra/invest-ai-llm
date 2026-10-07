"""Byte-stable OOXML (DOCX/PPTX/XLSX) post-processing.

The writers stamp the current time into zip entries, ``docProps/core.xml`` and DOCX
comment dates. :func:`normalize` rewrites the package with fixed timestamps.
:func:`add_cached_values` inserts cached formula results, which openpyxl never writes.
"""

import re
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final

FIXED_ZIP_TIME: Final = (2026, 9, 30, 10, 0, 0)
FIXED_ISO: Final = "2026-09-30T10:00:00Z"
_ISO: Final = re.compile(rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?")
_STAMPED: Final = ("docProps/core.xml", "word/comments.xml", "word/commentsExtended.xml")

Patch = Callable[[bytes], bytes]


def normalize(path: Path, patches: Mapping[str, Patch] | None = None) -> None:
    with zipfile.ZipFile(path) as src:
        entries = [(info.filename, src.read(info.filename)) for info in src.infolist()]
    with zipfile.ZipFile(path, "w") as dst:
        for name, original in entries:
            data = _ISO.sub(FIXED_ISO.encode(), original) if name in _STAMPED else original
            if patches and name in patches:
                data = patches[name](data)
            info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0o644 << 16
            dst.writestr(info, data, compresslevel=6)


_FORMULA_CELL: Final = re.compile(
    rb'<c r="([A-Z]+[0-9]+)"([^>]*)><f>(.*?)</f>(?:<v\s*/>|<v></v>|<v>[^<]*</v>)?</c>'
)


def add_cached_values(values: Mapping[str, float], uncached: set[str]) -> Patch:
    """Patch for one worksheet: write ``<v>`` for every formula cell in ``values`` and
    no value for those in ``uncached``. Any other formula cell is an error."""

    def patch(xml: bytes) -> bytes:
        seen: set[str] = set()

        def repl(m: re.Match[bytes]) -> bytes:
            ref = m.group(1).decode()
            seen.add(ref)
            head = b'<c r="' + m.group(1) + b'"' + m.group(2) + b"><f>" + m.group(3) + b"</f>"
            if ref in uncached:
                return head + b"</c>"
            if ref not in values:
                raise ValueError(
                    f"formula cell {ref} has no cached value and is not listed as uncached"
                )
            return head + b"<v>" + repr(float(values[ref])).encode() + b"</v></c>"

        out = _FORMULA_CELL.sub(repl, xml)
        missing = (set(values) | uncached) - seen
        if missing:
            raise ValueError(f"cached values for cells without formulas: {sorted(missing)[:5]}")
        return out

    return patch
