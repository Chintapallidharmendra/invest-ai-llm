"""What a target returned for one case, independent of how it was produced."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Source:
    document: str  # corpus file name (API ids are mapped back), or the raw id if unknown
    locator: str
    kind: str = "document"


@dataclass
class Table:
    columns: list[str]
    rows: list[list[Any]]
    title: str = ""
    origin: str = "event"  # "event" (engine table), "output" (downloaded file), "text" (parsed)


@dataclass
class TurnResult:
    text: str = ""
    sources: list[Source] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    finish_reason: str | None = None
    reason_code: str | None = None  # decline category / withheld reason, when given
    replaced: bool = False
    error: str | None = None
    jobs: list[dict[str, Any]] = field(default_factory=list)
    plans: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: int | None = None


@dataclass
class Transcript:
    turns: list[TurnResult] = field(default_factory=list)
    skipped: str | None = None  # e.g. "feature-unavailable", "mode-unsupported"
    error: str | None = None
    prompt_documents: list[str] = field(default_factory=list)

    @property
    def final(self) -> TurnResult:
        return self.turns[-1] if self.turns else TurnResult()
