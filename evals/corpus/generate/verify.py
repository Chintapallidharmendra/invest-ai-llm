"""Corpus self-check (Story 1.7, AC #1-#5).

``verify(build, manifest)`` returns a list of problems (empty means the corpus is
consistent). It checks:

- every file in ``build/`` is in the manifest and vice versa, with matching SHA-256;
- structure: page, slide, sheet and row counts, hidden sheets, image-only scans;
- every planted item, figure and canary is at its locator;
- the term-sheet difference list against both versions;
- workbook totals recomputed from the data cells, and the cached totals;
- the uncached formula really has no cached value;
- corpus composition minimums from the story.
"""

import csv
import hashlib
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, get_args

import pypdfium2 as pdfium  # type: ignore[import-untyped]
from docx import Document
from openpyxl import load_workbook  # type: ignore[import-untyped]
from openpyxl.utils.cell import range_boundaries  # type: ignore[import-untyped]
from pptx import Presentation

from generate.manifest_schema import (
    BenignType,
    DocumentEntry,
    IdentifierType,
    InjectionType,
    Manifest,
    PlantedItem,
)

MIN_IDENTIFIERS: Final = 60
MIN_BENIGN: Final = 40
MIN_INJECTIONS: Final = 20
MIN_FIGURES_PER_CIM: Final = 15
MIN_DIFFS: Final = 12
CIM_PAGES: Final = [60, 110, 150]


def collapse(text: str) -> str:
    return " ".join(text.split())


def squash(text: str) -> str:
    return "".join(text.split())


# --- readers (cached per file) -------------------------------------------------------------


class Pdf:
    def __init__(self, path: Path) -> None:
        doc = pdfium.PdfDocument(str(path))
        self.pages: list[str] = [
            str(doc[i].get_textpage().get_text_range()) for i in range(len(doc))
        ]
        self.images = [
            sum(1 for obj in doc[i].get_objects() if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE)
            for i in range(len(doc))
        ]
        meta = doc.get_metadata_dict()
        self.meta: dict[str, str] = {str(k).lower(): str(v) for k, v in meta.items()}
        doc.close()

    def at(self, locator: str) -> str:
        if locator.startswith("meta:"):
            return self.meta.get(locator[5:], "")
        page = int(locator.removeprefix("p."))
        return self.pages[page - 1]


class Docx:
    def __init__(self, path: Path) -> None:
        self.doc = Document(str(path))
        self.paragraphs = self.doc.paragraphs
        self.comments = list(self.doc.comments)
        body = self.doc.element.body
        self.page_breaks = len(body.xpath('.//w:br[@w:type="page"]'))

    def at(self, locator: str) -> str:
        if locator.startswith("meta:"):
            return str(getattr(self.doc.core_properties, locator[5:]) or "")
        if locator.startswith("para "):
            return self.paragraphs[int(locator[5:]) - 1].text
        if locator.startswith("comment "):
            return self.comments[int(locator[8:]) - 1].text
        m = re.fullmatch(r"table (\d+) r(\d+)c(\d+)", locator)
        if m:
            table = self.doc.tables[int(m[1]) - 1]
            return table.cell(int(m[2]) - 1, int(m[3]) - 1).text
        raise ValueError(f"bad DOCX locator {locator!r}")

    def run_style(self, locator: str, text: str) -> tuple[Any, Any]:
        paragraph = self.paragraphs[int(locator[5:]) - 1]
        run = next(r for r in paragraph.runs if text in r.text)
        color = run.font.color.rgb if run.font.color and run.font.color.type else None
        return color, run.font.size


class Pptx:
    def __init__(self, path: Path) -> None:
        self.prs = Presentation(str(path))
        self.slides: list[str] = []
        self.notes: list[str] = []
        for slide in self.prs.slides:
            texts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    texts.append(shape.text_frame.text)
                if getattr(shape, "has_table", False) and shape.has_table:
                    texts.extend(cell.text for row in shape.table.rows for cell in row.cells)
            self.slides.append("\n".join(texts))
            notes = slide.notes_slide.notes_text_frame.text if slide.has_notes_slide else ""
            self.notes.append(notes)

    def at(self, locator: str) -> str:
        if locator.startswith("meta:"):
            return str(getattr(self.prs.core_properties, locator[5:]) or "")
        m = re.fullmatch(r"slide (\d+)( notes)?", locator)
        if not m:
            raise ValueError(f"bad PPTX locator {locator!r}")
        n = int(m[1]) - 1
        return self.notes[n] if m[2] else self.slides[n]


class Xlsx:
    def __init__(self, path: Path) -> None:
        self.formulas = load_workbook(path)  # comments, formulas, sheet states
        self.values = load_workbook(path, data_only=True)  # cached values

    def at(self, locator: str) -> str:
        if locator.startswith("meta:"):
            return str(getattr(self.formulas.properties, locator[5:]) or "")
        if locator.startswith("sheetname:"):
            name = locator[len("sheetname:") :]
            return name if name in self.formulas.sheetnames else ""
        ref, _, extra = locator.partition(" ")
        sheet, _, cell = ref.rpartition("!")
        target = self.formulas[sheet][cell]
        if extra == "comment":
            return target.comment.text if target.comment else ""
        return "" if target.value is None else str(target.value)

    def cached(self, ref: str) -> Any:
        sheet, _, cell = ref.rpartition("!")
        return self.values[sheet][cell].value

    def range_values(self, ref: str) -> list[Any]:
        sheet, _, cells = ref.rpartition("!")
        min_col, min_row, max_col, max_row = range_boundaries(cells)
        ws = self.formulas[sheet]
        return [
            c.value
            for row in ws.iter_rows(
                min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col
            )
            for c in row
        ]


class Csv:
    def __init__(self, path: Path) -> None:
        with path.open(encoding="utf-8", newline="") as fh:
            self.rows = list(csv.reader(fh))

    def at(self, locator: str) -> str:
        m = re.fullmatch(r"([A-Z]+)(\d+)", locator)
        if not m:
            raise ValueError(f"bad CSV locator {locator!r}")
        col = ord(m[1]) - ord("A")
        return self.rows[int(m[2]) - 1][col]


Reader = Pdf | Docx | Pptx | Xlsx | Csv
_READERS: Final[dict[str, Callable[[Path], Reader]]] = {
    "pdf": Pdf,
    "docx": Docx,
    "pptx": Pptx,
    "xlsx": Xlsx,
    "csv": Csv,
}


# --- checks ------------------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _found(item: PlantedItem, text: str) -> bool:
    if item.match == "normalized":
        return squash(item.value) in squash(text)
    if item.match == "ocr":
        return item.value not in text and not text.strip()
    return collapse(item.value) in collapse(text)


def _structure(doc: DocumentEntry, reader: Reader) -> list[str]:
    problems = []
    if isinstance(reader, Pdf):
        if doc.pages != len(reader.pages):
            problems.append(f"{doc.file}: {len(reader.pages)} pages, manifest says {doc.pages}")
        has_text = any(t.strip() for t in reader.pages)
        if doc.image_only and (has_text or not all(reader.images)):
            problems.append(f"{doc.file}: expected image-only pages with no text layer")
        if not doc.image_only and not has_text:
            problems.append(f"{doc.file}: no text layer")
    elif isinstance(reader, Docx):
        if doc.pages != reader.page_breaks + 1:
            problems.append(
                f"{doc.file}: {reader.page_breaks + 1} pages by breaks, manifest says {doc.pages}"
            )
    elif isinstance(reader, Pptx):
        if doc.slides != len(reader.slides):
            problems.append(f"{doc.file}: {len(reader.slides)} slides, manifest says {doc.slides}")
        if not all(n.strip() for n in reader.notes):
            problems.append(f"{doc.file}: a slide has no speaker notes")
    elif isinstance(reader, Xlsx):
        wb = reader.formulas
        if doc.sheets != wb.sheetnames:
            problems.append(f"{doc.file}: sheets {wb.sheetnames} != {doc.sheets}")
        hidden = [ws.title for ws in wb.worksheets if ws.sheet_state != "visible"]
        if hidden != doc.hidden_sheets:
            problems.append(f"{doc.file}: hidden sheets {hidden} != {doc.hidden_sheets}")
        rows = {ws.title: ws.max_row for ws in wb.worksheets}
        if doc.rows != rows:
            problems.append(f"{doc.file}: row counts differ from manifest")
        for ref in doc.uncached_formulas:
            sheet, _, cell = ref.rpartition("!")
            formula = wb[sheet][cell].value
            if (
                not (isinstance(formula, str) and formula.startswith("="))
                or reader.cached(ref) is not None
            ):
                problems.append(f"{doc.file}: {ref} should be a formula without a cached value")
    elif isinstance(reader, Csv) and doc.rows != {"csv": len(reader.rows) - 1}:
        problems.append(f"{doc.file}: {len(reader.rows) - 1} data rows, manifest says {doc.rows}")
    return problems


def _injection_style(item: PlantedItem, reader: Reader) -> list[str]:
    """DOCX white/tiny runs must really be white / 1pt."""
    if not isinstance(reader, Docx) or item.type not in {"white_text", "tiny_text"}:
        return []
    color, size = reader.run_style(item.locator, item.value)
    if item.type == "white_text" and str(color) != "FFFFFF":
        return [f"{item.id}: white_text run is not white ({color})"]
    if item.type == "tiny_text" and (size is None or size.pt > 2):
        return [f"{item.id}: tiny_text run is not tiny ({size})"]
    return []


def _totals(manifest: Manifest, readers: dict[str, Reader]) -> list[str]:
    problems = []
    for total in manifest.workbook_totals:
        reader = readers[total.doc]
        assert isinstance(reader, Xlsx)
        values = reader.range_values(total.source_range)
        if total.method == "count":
            computed = float(sum(1 for v in values if v not in (None, "")))
        else:
            computed = sum(v for v in values if isinstance(v, int | float))
        if abs(computed - total.value) > 1e-6 * max(1.0, abs(total.value)):
            problems.append(
                f"{total.doc}: {total.metric} recomputes to {computed}, manifest {total.value}"
            )
        if total.cell is not None:
            cached = reader.cached(total.cell)
            if not isinstance(cached, int | float) or abs(cached - total.value) > 1e-6 * max(
                1.0, abs(total.value)
            ):
                problems.append(f"{total.doc}: cached {total.cell}={cached} != {total.value}")
    return problems


def _diffs(manifest: Manifest, readers: dict[str, Reader]) -> list[str]:
    v3, v4 = readers.get("term_sheet_v3.docx"), readers.get("term_sheet_v4.docx")
    if not isinstance(v3, Docx) or not isinstance(v4, Docx):
        return ["term sheets v3/v4 missing"]
    text3 = collapse("\n".join(p.text for p in v3.paragraphs))
    text4 = collapse("\n".join(p.text for p in v4.paragraphs))
    problems = []
    for d in manifest.term_sheet_diff:
        if d.v3 is not None and (collapse(d.v3) not in text3 or collapse(d.v3) in text4):
            problems.append(f"{d.id}: v3 text must be in v3 only")
        if d.v4 is not None and (collapse(d.v4) not in text4 or collapse(d.v4) in text3):
            problems.append(f"{d.id}: v4 text must be in v4 only")
    return problems


def _composition(manifest: Manifest) -> list[str]:
    problems = []
    items = manifest.items
    kinds = Counter(i.kind for i in items)
    if kinds["identifier"] < MIN_IDENTIFIERS:
        problems.append(f"only {kinds['identifier']} identifiers (< {MIN_IDENTIFIERS})")
    if kinds["benign"] < MIN_BENIGN:
        problems.append(f"only {kinds['benign']} benign items (< {MIN_BENIGN})")
    if kinds["injection"] < MIN_INJECTIONS:
        problems.append(f"only {kinds['injection']} injections (< {MIN_INJECTIONS})")
    for kind, types in (
        ("identifier", get_args(IdentifierType)),
        ("benign", get_args(BenignType)),
        ("injection", get_args(InjectionType)),
    ):
        present = {i.type for i in items if i.kind == kind}
        missing = set(types) - present
        if missing:
            problems.append(f"no {kind} of type {sorted(missing)}")
        unknown = present - set(types)
        if unknown:
            problems.append(f"unknown {kind} types {sorted(unknown)}")
    variants = {i.variant for i in items if i.kind == "identifier"}
    for needed in (
        "masked",
        "split-line",
        "spaced",
        "lower",
        "hyphenated",
        "contiguous",
        "sheet-name",
    ):
        if needed not in variants:
            problems.append(f"no identifier with variant {needed!r}")

    docs = manifest.documents
    canaries = [d.canary for d in docs]
    if len(set(canaries)) != len(docs):
        problems.append("canaries are not unique per document")
    planted_canaries = {i.doc: i.value for i in items if i.kind == "canary"}
    for d in docs:
        if planted_canaries.get(d.file) != d.canary:
            problems.append(f"{d.file}: canary not planted exactly once")
    cims = [d for d in docs if d.file.startswith("cim_")]
    if sorted(d.pages or 0 for d in cims) != CIM_PAGES:
        problems.append(f"CIM page counts {[d.pages for d in cims]} != {CIM_PAGES}")
    for d in cims:
        if len(d.figures) < MIN_FIGURES_PER_CIM:
            problems.append(f"{d.file}: {len(d.figures)} figures (< {MIN_FIGURES_PER_CIM})")
    by_format = Counter(d.format for d in docs if not d.image_only)
    expected = {"pdf": 3, "docx": 3, "pptx": 1, "xlsx": 3, "csv": 1}
    if dict(by_format) != expected:
        problems.append(f"document mix {dict(by_format)} != {expected}")
    if sum(1 for d in docs if d.image_only) != 2:
        problems.append("expected 2 scanned (image-only) PDFs")
    xlsx = [d for d in docs if d.format == "xlsx"]
    if not any(max((d.rows or {}).values(), default=0) >= 50_001 for d in xlsx):
        problems.append("no sheet with >= 50,000 data rows")
    if sum(len(d.hidden_sheets) for d in xlsx) < 1:
        problems.append("no hidden sheet")
    if sum(len(d.uncached_formulas) for d in xlsx) != 1:
        problems.append("expected exactly one formula without a cached value")
    for d in xlsx:
        if not {"P&L", "Segments", "KPIs"} <= set(d.sheets or []):
            problems.append(f"{d.file}: missing P&L/Segments/KPIs sheets")
    spa = next((d for d in docs if d.file.startswith("spa_")), None)
    if spa is None or spa.pages != 40:
        problems.append("SPA must have 40 pages")
    deck = next((d for d in docs if d.format == "pptx"), None)
    if deck is None or deck.slides != 25:
        problems.append("deck must have 25 slides")
    diffs = manifest.term_sheet_diff
    if len(diffs) < MIN_DIFFS or {d.change for d in diffs} != {"added", "removed", "changed"}:
        problems.append("term-sheet diff list needs >= 12 entries covering added/removed/changed")
    if len({i.id for i in items}) != len(items):
        problems.append("duplicate item ids")
    return problems


def verify(build: Path, manifest: Manifest) -> list[str]:
    problems: list[str] = []
    on_disk = {p.name for p in build.iterdir()} if build.is_dir() else set()
    listed = {d.file for d in manifest.documents}
    problems += [f"{name}: in build/ but not in manifest.yaml" for name in sorted(on_disk - listed)]
    problems += [
        f"{name}: in manifest.yaml but missing from build/" for name in sorted(listed - on_disk)
    ]
    problems += _composition(manifest)
    if problems:
        return problems

    readers: dict[str, Reader] = {}
    for doc in manifest.documents:
        path = build / doc.file
        if _sha256(path) != doc.sha256 or path.stat().st_size != doc.bytes:
            problems.append(
                f"{doc.file}: SHA-256/size differs from manifest (rebuild with `make corpus`)"
            )
            continue  # contents are unknown; deeper checks would only add noise
        reader = _READERS[doc.format](path)
        readers[doc.file] = reader
        problems += _structure(doc, reader)
        for figure in doc.figures:
            if collapse(figure.text) not in collapse(reader.at(figure.locator)):
                problems.append(
                    f"{doc.file}: figure {figure.label!r} ({figure.text}) not at {figure.locator}"
                )

    if len(readers) != len(manifest.documents):
        return problems
    for item in manifest.items:
        reader = readers[item.doc]
        try:
            text = reader.at(item.locator)
        except (KeyError, IndexError, ValueError) as exc:
            problems.append(f"{item.id}: bad locator {item.locator!r} ({exc})")
            continue
        if not _found(item, text):
            problems.append(f"{item.id}: {item.type} not found at {item.locator}")
        problems += _injection_style(item, reader)

    problems += _totals(manifest, readers)
    problems += _diffs(manifest, readers)
    return problems
