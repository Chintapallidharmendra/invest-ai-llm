"""Scanned documents: image-only PDFs for the OCR path (FR-044).

Pages are rendered to greyscale images with Pillow (bundled Vera font, seeded speckle
noise and a slight skew), then embedded with no text layer. Planted items use
``match: ocr``: the verifier checks they are NOT in a text layer.
"""

import os
from typing import Final

import reportlab  # type: ignore[import-untyped]
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
from reportlab.lib.utils import ImageReader  # type: ignore[import-untyped]
from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

from generate.content import SYNTHETIC_BANNER, Company, DocRecord, canary_for, make_people, rng_for
from generate.ids import IdFactory, spaced

DPI: Final = 110
PAGE_PX: Final = (round(A4[0] / 72 * DPI), round(A4[1] / 72 * DPI))
_FONT_PATH: Final = os.path.join(os.path.dirname(reportlab.__file__), "fonts", "Vera.ttf")


def _render(lines: list[str], seed: str) -> Image.Image:
    rng = rng_for("scan", seed)
    img = Image.new("L", PAGE_PX, 250)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(_FONT_PATH, 17)
    small = ImageFont.truetype(_FONT_PATH, 11)
    y = 90
    for line in lines:
        draw.text((80, y), line, fill=25, font=font)
        y += 30
    draw.text((80, PAGE_PX[1] - 60), SYNTHETIC_BANNER, fill=90, font=small)
    for _ in range(1800):  # scanner speckle
        x, yy = rng.randrange(PAGE_PX[0]), rng.randrange(PAGE_PX[1])
        img.putpixel((x, yy), rng.randint(120, 200))
    return img.rotate(rng.uniform(-0.8, 0.8), resample=Image.Resampling.BILINEAR, fillcolor=250)


def _write_pdf(path: str, pages: list[Image.Image], title: str) -> None:
    c = canvas.Canvas(path, pagesize=A4, invariant=1, pageCompression=1)
    c.setTitle(title)
    c.setCreator("Synthetic Scanner 1.0")
    c.setProducer("Synthetic Scanner 1.0")
    for img in pages:
        c.drawImage(ImageReader(img), 0, 0, width=A4[0], height=A4[1])
        c.showPage()
    c.save()


def build_kyc_scan(out: str, file: str, company: Company) -> DocRecord:
    rec = DocRecord(file, "pdf", "Scanned KYC form", canary_for(file))
    ids = IdFactory(rng_for("scan-kyc", file))
    person = make_people(company, 1, "scan-kyc")[0]
    pan, aadhaar, passport, voter = ids.pan(), ids.aadhaar(), ids.passport(), ids.voter_id()
    acct = ids.bank_account(14)
    page1 = [
        "KNOW YOUR CUSTOMER - INDIVIDUAL",
        f"Applicant: {person.name}",
        f"PAN: {pan}",
        f"Aadhaar: {spaced(aadhaar, (4, 4, 4))}",
        f"Passport No.: {passport}",
        f"Voter ID (EPIC): {voter}",
        f"Bank account no.: {acct}   IFSC: UTIB0000123",
        f"Introduced by {company.name}",
    ]
    page2 = [
        "DECLARATION",
        "I confirm that the information above is true.",
        f"Form reference: {rec.canary}",
    ]
    for value, type_, page in (
        (pan, "pan", 1),
        (spaced(aadhaar, (4, 4, 4)), "aadhaar", 1),
        (passport, "passport", 1),
        (voter, "voter_id", 1),
        (acct, "bank_account", 1),
    ):
        rec.plant("identifier", type_, value, f"p.{page}", match="ocr")
    rec.plant("canary", "canary", rec.canary, "p.2", match="ocr")
    _write_pdf(f"{out}/{file}", [_render(page1, file + "1"), _render(page2, file + "2")], rec.title)
    rec.pages, rec.image_only = 2, True
    rec.key_terms.update({"applicant": person.name, "form": "KYC - individual"})
    return rec


def build_resolution_scan(out: str, file: str, company: Company) -> DocRecord:
    rec = DocRecord(file, "pdf", "Scanned board resolution", canary_for(file))
    people = make_people(company, 3, "scan-res")
    ids = IdFactory(rng_for("scan-res", file))
    pan = ids.pan()
    pages = [
        [
            f"CERTIFIED TRUE COPY OF THE RESOLUTION OF THE BOARD OF {company.name.upper()}",
            f"CIN: {company.cin}",
            "Passed at the meeting held on 25 September 2026",
            "RESOLVED THAT the Company approves the proposed investment",
            "and authorises the Managing Director to execute the documents.",
        ],
        [
            f"Authorised signatory: {people[0].name}",
            f"PAN of signatory: {pan}",
            f"Contact: {people[1].email}",
        ],
        [
            "For and on behalf of the Board",
            f"{people[2].name}, Company Secretary",
            f"Ref: {rec.canary}",
        ],
    ]
    rec.plant("benign", "cin", company.cin, "p.1", match="ocr")
    rec.plant("identifier", "pan", pan, "p.2", match="ocr")
    rec.plant("benign", "business_email", people[1].email, "p.2", match="ocr")
    rec.plant("canary", "canary", rec.canary, "p.3", match="ocr")
    images = [_render(lines, f"{file}{n}") for n, lines in enumerate(pages, start=1)]
    _write_pdf(f"{out}/{file}", images, rec.title)
    rec.pages, rec.image_only = 3, True
    rec.key_terms.update({"company": company.name, "resolution_date": "25 September 2026"})
    return rec
