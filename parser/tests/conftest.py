"""Shared fixtures: the synthetic corpus (Story 1.7) and generated hostile samples.

The corpus files are generated (``make corpus``), not committed; tests that need them
skip when ``evals/corpus/build`` is missing.
"""

import io
import os
import sys
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

if sys.platform == "darwin":
    # macOS aborts a forked child that touches Objective-C state initialised before the
    # fork (the forkserver preloads Docling). Linux, where the service runs, doesn't.
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

REPO: Final = Path(__file__).resolve().parents[2]
CORPUS: Final = REPO / "evals" / "corpus"
BUILD: Final = CORPUS / "build"


@pytest.fixture(scope="session")
def manifest() -> dict[str, Any]:
    if not BUILD.is_dir():
        pytest.skip("corpus not built (run `make corpus`)")
    data: dict[str, Any] = yaml.safe_load((CORPUS / "manifest.yaml").read_text())
    return data


def corpus_file(name: str) -> bytes:
    path = BUILD / name
    if not path.is_file():
        pytest.skip(f"corpus file missing: {name} (run `make corpus`)")
    return path.read_bytes()


def document(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    return next(d for d in manifest["documents"] if d["file"] == name)


def pdf_bytes(pages: int, *, text: bool = True) -> bytes:
    """A minimal PDF with ``pages`` pages (each with a line of text unless ``text`` is
    False), built by hand so tests don't need a PDF writer."""
    objects: list[bytes] = []
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    font_id = 3 + 2 * pages
    for i in range(pages):
        content = f"BT /F1 12 Tf 72 720 Td (Page {i + 1} text) Tj ET".encode() if text else b""
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    )
    return out.getvalue()
