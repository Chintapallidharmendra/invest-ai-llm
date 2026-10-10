"""Spike command line. Each measuring command merges its result into one candidate's
entry in ``results.json``; ``report`` renders ``REPORT.md`` from it::

    python -m evals.spike start      --results R --candidate Qwen3-14B-AWQ --quant AWQ-INT4
    python -m evals.spike quality    --results R --candidate Qwen3-14B-AWQ --eval-results evals/reports/<ts>/results.json
    python -m evals.spike tool-calls --results R --candidate Qwen3-14B-AWQ --target http://vllm-chat:8000/v1
    python -m evals.spike load       --results R --candidate ... --target ... --levels 1,5,10
    python -m evals.spike alongside  --results R --candidate ... --target ... --chats 10 --summaries 2
    python -m evals.spike soak       --results R --candidate ... --target ... --minutes 30
    python -m evals.spike features   --results R --candidate ... --target ...
    python -m evals.spike report     --results R --out evals/spike/REPORT.md

Exit codes: 0 ok, 2 usage error.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evals.spike import features, load, report
from evals.spike.client import Endpoint
from evals.spike.tool_calls import runner


def load_results(path: Path) -> dict[str, Any]:
    if path.is_file():
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data
    return {"spike": {}, "candidates": []}


def save_results(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def candidate(data: dict[str, Any], name: str) -> dict[str, Any]:
    for entry in data["candidates"]:
        if entry["name"] == name:
            return entry  # type: ignore[no-any-return]
    entry = {"name": name}
    data["candidates"].append(entry)
    return entry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.spike")
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str, *, target: bool = True) -> argparse.ArgumentParser:
        p = sub.add_parser(name)
        p.add_argument("--results", type=Path, required=True)
        if name != "report":
            p.add_argument("--candidate", required=True)
        if target:
            p.add_argument("--target", required=True, help="e.g. http://vllm-chat:8000/v1")
            p.add_argument("--model", default="chat", help="served model name")
        return p

    start = command("start", target=False)
    start.add_argument("--quant", default="?")
    start.add_argument("--max-model-len", type=int, default=16384)
    start.add_argument("--max-num-seqs", type=int, default=16)
    start.add_argument("--failed", default=None, help="the start error, if it didn't start")

    quality = command("quality", target=False)
    quality.add_argument("--eval-results", type=Path, required=True)

    command("tool-calls")
    lv = command("load")
    lv.add_argument("--levels", default="1,5,10")
    lv.add_argument("--rounds", type=int, default=3)
    al = command("alongside")
    al.add_argument("--chats", type=int, default=10)
    al.add_argument("--summaries", type=int, default=2)
    al.add_argument("--rounds", type=int, default=3)
    sk = command("soak")
    sk.add_argument("--minutes", type=float, default=30)
    sk.add_argument("--chats", type=int, default=10)
    sk.add_argument("--summaries", type=int, default=2)
    ft = command("features")
    ft.add_argument("--probes", default=",".join(features.PROBES))

    rp = command("report", target=False)
    rp.add_argument("--out", type=Path, required=True)
    for key in ("host", "gpu", "date", "vllm-image", "git-commit", "guard", "gpu-split"):
        rp.add_argument(f"--{key}", default=None)
    return parser


async def _measure(args: argparse.Namespace, endpoint: Endpoint) -> tuple[str, Any]:
    match args.command:
        case "tool-calls":
            return "tool_calls", await runner.run(endpoint)
        case "load":
            levels = [int(x) for x in args.levels.split(",")]
            return "levels", [await load.run_level(endpoint, n, rounds=args.rounds) for n in levels]
        case "alongside":
            return "with_summaries", await load.chat_with_summaries(
                endpoint, chats=args.chats, summaries=args.summaries, rounds=args.rounds
            )
        case "soak":
            return "soak", await load.soak(
                endpoint, duration_s=args.minutes * 60, chats=args.chats, summaries=args.summaries
            )
        case "features":
            return "features", await features.run_probes(endpoint, args.probes.split(","))
    raise SystemExit(2)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    data = load_results(args.results)

    if args.command == "report":
        spike = data.setdefault("spike", {})
        for key in ("host", "gpu", "date", "vllm_image", "git_commit", "guard", "gpu_split"):
            value = getattr(args, key)
            if value is not None:
                spike[key] = value
        save_results(args.results, data)
        args.out.write_text(report.render(data), encoding="utf-8")
        print(f"wrote {args.out}")
        return 0

    entry = candidate(data, args.candidate)
    if args.command == "start":
        entry.update(
            quantisation=args.quant,
            max_model_len=args.max_model_len,
            max_num_seqs=args.max_num_seqs,
            started=args.failed is None,
            start_error=args.failed,
        )
    elif args.command == "quality":
        evals = json.loads(args.eval_results.read_text(encoding="utf-8"))
        # Summaries only: per-case transcripts stay in the git-ignored eval report.
        entry["quality"] = {"overall": evals["overall"], "categories": evals["categories"]}
    else:
        endpoint = Endpoint(args.target, args.model)
        key, value = asyncio.run(_measure(args, endpoint))
        if key in {"levels", "with_summaries"}:
            entry.setdefault("performance", {})[key] = value
        else:
            entry[key] = value
    save_results(args.results, data)
    print(f"{args.command}: saved to {args.results}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
