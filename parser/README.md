# parser

Separate container that turns uploaded files (PDF, Word, PowerPoint, Excel/CSV) into
structured text and tables using Docling, RapidOCR (for scans) and openpyxl.

- Has **no access** to the application database and **no network**; it runs under rlimits
  (ADR-027).
- Only `backend/app/documents/ingestion` talks to it (ADR-038).
- Dependencies are declared in `pyproject.toml` and pinned in `uv.lock`. Do not edit them
  in feature stories.

The entrypoint and Dockerfile arrive in later stories.
