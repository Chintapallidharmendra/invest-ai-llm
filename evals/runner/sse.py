"""SSE parsing and reduction for the chat stream (ADR-003, ADR-025).

Events: ``meta``, ``status``, ``delta``, ``table``, ``sources``, ``replace``, ``plan``,
``job``, ``done``, ``error``. A ``replace`` withdraws the streamed text, so the final
text is what remains after the last ``replace`` plus later deltas.
"""

import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from evals.runner.transcript import Source, Table, TurnResult


@dataclass(frozen=True)
class Event:
    name: str
    data: dict[str, Any]


def parse(lines: Iterable[str]) -> Iterator[Event]:
    """Parse ``text/event-stream`` lines (without trailing newlines) into events."""
    name = "message"
    data: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r")
        if not line:
            if data:
                payload = "\n".join(data)
                try:
                    parsed = json.loads(payload)
                except json.JSONDecodeError:
                    parsed = {"raw": payload}
                yield Event(name, parsed if isinstance(parsed, dict) else {"value": parsed})
            name, data = "message", []  # reset after each event
            continue
        if line.startswith(":"):
            continue  # comment / keep-alive
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field == "event":
            name = value
        elif field == "data":
            data.append(value)
    if data:
        yield Event(name, json.loads("\n".join(data)))


def table_from(data: dict[str, Any], origin: str = "event") -> Table:
    columns = [c["name"] if isinstance(c, dict) else str(c) for c in data.get("columns", [])]
    rows = [list(r.values()) if isinstance(r, dict) else list(r) for r in data.get("rows", [])]
    return Table(columns=columns, rows=rows, title=str(data.get("title", "")), origin=origin)


def reduce(events: Iterable[Event], resolve_document: Callable[[str], str] = str) -> TurnResult:
    """Fold a turn's events into its final state."""
    turn = TurnResult()
    for event in events:
        d = event.data
        if event.name == "delta":
            turn.text += str(d.get("text", ""))
        elif event.name == "replace":
            turn.text = str(d.get("text", ""))
            turn.replaced = True
            turn.reason_code = d.get("reason_code") or turn.reason_code
        elif event.name == "sources":
            for item in d.get("items", []):
                doc = item.get("document_id")
                turn.sources.append(
                    Source(
                        document=resolve_document(str(doc)) if doc else "",
                        locator=str(item.get("locator") or item.get("page") or ""),
                        kind=str(item.get("kind", "document")),
                    )
                )
        elif event.name == "table":
            turn.tables.append(table_from(d))
        elif event.name == "plan":
            turn.plans.append(d)
        elif event.name == "job":
            turn.jobs.append(d)
        elif event.name == "done":
            turn.finish_reason = d.get("finish_reason")
            turn.reason_code = d.get("reason_code") or turn.reason_code
            if isinstance(d.get("latency_ms"), int):
                turn.latency_ms = d["latency_ms"]
        elif event.name == "error":
            problem = d.get("problem", d)
            turn.error = str(problem.get("code") or problem.get("title") or "error")
    return turn
