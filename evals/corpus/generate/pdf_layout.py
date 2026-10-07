"""A small deterministic PDF layout engine on the reportlab canvas.

Platypus hides page numbers until layout time; drawing lines ourselves means every
planted item's page is known exactly when it is drawn. ``invariant=1`` fixes the
document ID and timestamps, so output is byte-stable.

Fonts: the bundled Bitstream Vera TTFs (embedded). They have no rupee glyph, so PDFs
write amounts as "Rs." / "INR"; the rupee sign is used in DOCX, PPTX, XLSX and CSV.
"""

import os
from collections.abc import Sequence
from typing import Final

import reportlab  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
from reportlab.lib.utils import simpleSplit  # type: ignore[import-untyped]
from reportlab.pdfbase import pdfmetrics  # type: ignore[import-untyped]
from reportlab.pdfbase.ttfonts import TTFont  # type: ignore[import-untyped]
from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

from generate.content import SYNTHETIC_BANNER

FONT: Final = "Vera"
BOLD: Final = "VeraBd"
_FONTS_DIR: Final = os.path.join(os.path.dirname(reportlab.__file__), "fonts")

for _name, _file in ((FONT, "Vera.ttf"), (BOLD, "VeraBd.ttf")):
    if _name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(_name, os.path.join(_FONTS_DIR, _file)))

WIDTH, HEIGHT = A4
MARGIN: Final = 56.0
TOP: Final = HEIGHT - 72.0
BOTTOM: Final = 64.0


class PdfPages:
    def __init__(
        self,
        path: str,
        *,
        title: str,
        header: str,
        author: str,
        subject: str = "",
        keywords: str = "",
    ) -> None:
        self.c = canvas.Canvas(path, pagesize=A4, invariant=1, pageCompression=1)
        self.c.setTitle(title)
        self.c.setAuthor(author)
        self.c.setSubject(subject)
        self.c.setKeywords(keywords)
        self.c.setCreator("invest-ai-llm synthetic corpus generator")
        self.c.setProducer("invest-ai-llm synthetic corpus generator")
        self.header = header
        self.page = 1
        self.y = TOP
        self._decorate()

    # --- pages ----------------------------------------------------------------------

    def _decorate(self) -> None:
        c = self.c
        c.setFillGray(0.35)
        c.setFont(FONT, 7.5)
        c.drawString(MARGIN, HEIGHT - 40, self.header)
        c.drawRightString(WIDTH - MARGIN, HEIGHT - 40, "Strictly private and confidential")
        c.drawString(MARGIN, 36, SYNTHETIC_BANNER)
        c.drawRightString(WIDTH - MARGIN, 36, f"Page {self.page}")
        c.setFillGray(0)
        self.y = TOP

    def new_page(self) -> int:
        self.c.showPage()
        self.page += 1
        self._decorate()
        return self.page

    def ensure(self, height: float) -> None:
        if self.y - height < BOTTOM:
            self.new_page()

    def save(self) -> int:
        self.c.showPage()
        self.c.save()
        return self.page

    # --- text -----------------------------------------------------------------------

    def heading(self, text: str, size: float = 15) -> int:
        self.ensure(size * 2.4)
        self.y -= size * 0.6
        self.c.setFont(BOLD, size)
        self.c.drawString(MARGIN, self.y, text)
        self.y -= size * 1.2
        return self.page

    def para(self, text: str, size: float = 9.5, leading: float = 13.5) -> int:
        """Wrapped paragraph (may continue on the next page). Returns the start page."""
        lines = simpleSplit(text, FONT, size, WIDTH - 2 * MARGIN)
        self.ensure(leading)
        start = self.page
        for line in lines:
            self.ensure(leading)
            self.c.setFont(FONT, size)
            self.c.drawString(MARGIN, self.y, line)
            self.y -= leading
        self.y -= leading * 0.5
        return start

    def line(self, text: str, size: float = 9.5, leading: float = 13.5, bold: bool = False) -> int:
        """One unwrapped line, kept on a single page. Returns its page."""
        self.ensure(leading)
        self.c.setFont(BOLD if bold else FONT, size)
        self.c.drawString(MARGIN, self.y, text)
        self.y -= leading
        return self.page

    def split_line(self, before: str, after: str, size: float = 9.5, leading: float = 13.5) -> int:
        """Two consecutive lines on the same page (an identifier broken across them)."""
        self.ensure(2 * leading)
        page = self.line(before, size, leading)
        self.line(after, size, leading)
        return page

    def white_text(self, text: str) -> int:
        """White-on-white text: invisible, but in the text layer."""
        self.ensure(12)
        self.c.setFillColorRGB(1, 1, 1)
        self.c.setFont(FONT, 8)
        self.c.drawString(MARGIN, self.y, text)
        self.c.setFillGray(0)
        self.y -= 12
        return self.page

    def tiny_text(self, text: str) -> int:
        """1-point text: practically invisible, but in the text layer."""
        self.ensure(3)
        self.c.setFont(FONT, 1)
        self.c.drawString(MARGIN, self.y, text)
        self.y -= 3
        return self.page

    # --- tables -----------------------------------------------------------------------

    def table(
        self,
        header: Sequence[str],
        rows: Sequence[Sequence[str]],
        widths: Sequence[float],
        *,
        size: float = 8,
        row_height: float = 12.5,
        title: str | None = None,
    ) -> list[int]:
        """A table that continues across pages, repeating its header. Returns the page
        of each row."""
        pages: list[int] = []

        def draw_header() -> None:
            self.c.setFont(BOLD, size)
            self._row(header, widths, row_height)
            self.c.setLineWidth(0.5)
            self.c.line(
                MARGIN, self.y + row_height - 3, MARGIN + sum(widths), self.y + row_height - 3
            )

        if title:
            self.ensure(row_height * 4)
            self.line(title, size + 1.5, bold=True)
        self.ensure(row_height * 3)
        draw_header()
        for row in rows:
            if self.y - row_height < BOTTOM:
                self.new_page()
                if title:
                    self.line(f"{title} (continued)", size + 1.5, bold=True)
                draw_header()
            self.c.setFont(FONT, size)
            self._row(row, widths, row_height)
            pages.append(self.page)
        self.y -= row_height * 0.6
        return pages

    def _row(self, cells: Sequence[str], widths: Sequence[float], row_height: float) -> None:
        x = MARGIN
        for i, (cell, width) in enumerate(zip(cells, widths, strict=True)):
            if i == 0:
                self.c.drawString(x, self.y, cell)
            else:
                self.c.drawRightString(x + width - 4, self.y, cell)
            x += width
        self.y -= row_height
