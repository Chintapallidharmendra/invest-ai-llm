"""Output settings (ADR-028).

Environment (``APP_OUTPUTS_`` prefix), e.g. ``APP_OUTPUTS_FILES_DIR=/data/files``.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field
from pydantic_settings import BaseSettings

from app.core.config import settings_config


class OutputsSettings(BaseSettings):
    model_config = settings_config("APP_OUTPUTS_")

    # Encrypted files live at <files_dir>/<space_id>/<output_id>.
    files_dir: Path = Path("/data/files")
    # Download links (FR-024: 24 h).
    link_ttl_s: Annotated[int, Field(gt=0)] = 24 * 3600
    # XLSX and DOCX are watermarked in memory (they can't be streamed), so their size is
    # bounded; CSV streams and has no such bound.
    max_watermark_bytes: Annotated[int, Field(gt=0)] = 50 * 1024 * 1024


@lru_cache
def get_outputs_settings() -> OutputsSettings:
    return OutputsSettings()
