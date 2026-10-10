"""Run the tool-call prompts against one endpoint and score every reply."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

import httpx
import yaml

from evals.spike.client import Endpoint, chat_body, complete
from evals.spike.tool_calls.scorer import Expectation, Score, score, summarise
from evals.spike.tool_calls.tools import TOOLS

PROMPTS: Final = Path(__file__).with_name("prompts.yaml")
MIN_PROMPTS: Final = 30


@dataclass(frozen=True)
class Case:
    id: str
    prompt: str
    expected: Expectation


def load_cases(path: Path = PROMPTS) -> tuple[str, list[Case]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = [
        Case(
            id=c["id"],
            prompt=c["prompt"],
            expected=Expectation(c["expect"].get("tool"), c["expect"].get("args") or {}),
        )
        for c in data["cases"]
    ]
    return data["system"], cases


async def run(
    endpoint: Endpoint, *, cases: Sequence[Case] | None = None, system: str | None = None
) -> dict[str, Any]:
    default_system, default_cases = load_cases()
    system = system or default_system
    cases = list(cases or default_cases)
    results: list[tuple[Case, Score]] = []
    async with httpx.AsyncClient(timeout=120) as client:
        for case in cases:
            body = chat_body(
                endpoint,
                [{"role": "system", "content": system}, {"role": "user", "content": case.prompt}],
                max_tokens=512,
                tools=TOOLS,
            )
            try:
                reply = await complete(client, endpoint, body)
                message = reply["choices"][0]["message"]
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                message = {"content": f"<request failed: {type(exc).__name__}>"}
            results.append((case, score(message, case.expected)))
    summary = summarise([s for _, s in results])
    return {
        "summary": {
            **asdict(summary),
            "valid_rate": summary.rate("valid"),
            "right_tool_rate": summary.rate("right_tool"),
            "right_args_rate": summary.rate("right_args"),
        },
        "cases": [
            {"id": case.id, **asdict(s), "failure": s.failure.value if s.failure else None}
            for case, s in results
        ],
    }
