"""Command line (Story 1.8, AC #3).

    python -m evals.runner run --mode model --target http://vllm-chat:8000/v1 --model chat
    python -m evals.runner run --mode api --target https://localhost:8043 --username eval-user
    python -m evals.runner count        # cases per category against the story minimums
    python -m evals.runner validate     # schema-check every case file
    python -m evals.runner author       # regenerate cases from the corpus manifest

Exit codes: 0 ok, 1 an enabled gate failed (or counts below minimum), 2 usage or setup error.
"""

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from evals.runner import corpus
from evals.runner.clients import FeatureUnavailable, Target
from evals.runner.judge import Judge, apply_judgement, load_overrides
from evals.runner.report import evaluate_gates, load_gates, write_report
from evals.runner.schema import REPO_ROOT, Case, CaseError, load_cases
from evals.runner.scoring import CaseResult, grounding_method, score
from evals.runner.transcript import Transcript

DEFAULT_CASES: Final = "evals/cases/eval/*.yaml"
MINIMUMS: Final = {
    "qa": 20,
    "summary": 12,
    "comparison": 6,
    "table_extract": 8,
    "excel_query": 10,
    "excel_op": 10,
    "not_found": 6,
    "multi_turn": 6,
    "concept": 2,
}
MIN_TOTAL: Final = 80


def _git_commit() -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        out = subprocess.run(  # noqa: S603 (fixed argv)
            [git, "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return out.stdout.strip() or "unknown"


def count(cases: list[Case]) -> tuple[Counter[str], list[str]]:
    counts = Counter[str](str(c.category) for c in cases)
    problems = [f"{cat}: {counts[cat]} < {n}" for cat, n in MINIMUMS.items() if counts[cat] < n]
    if len(cases) < MIN_TOTAL:
        problems.append(f"total: {len(cases)} < {MIN_TOTAL}")
    return counts, problems


def run_cases(
    cases: list[Case], target: Target, judge: Judge | None, *, progress: bool = True
) -> list[CaseResult]:
    overrides = load_overrides()
    results = []
    for n, case in enumerate(cases, start=1):
        if target.mode not in case.modes:
            transcript = Transcript(skipped="mode-unsupported")
        else:
            try:
                transcript = target.run_case(case)
            except FeatureUnavailable as exc:
                transcript = Transcript(skipped=f"feature-unavailable ({exc})")
            except Exception as exc:
                transcript = Transcript(error=f"{type(exc).__name__}: {str(exc)[:200]}")
        result = score(case, transcript)
        apply_judgement(result, case, transcript, judge, overrides)
        results.append(result)
        if progress:
            print(f"[{n}/{len(cases)}] {case.id}: {result.status}", file=sys.stderr)
    return results


def _target(args: argparse.Namespace) -> Target:
    if args.mode == "model":
        from evals.runner.clients.model import ModelTarget  # noqa: PLC0415

        extra = json.loads(args.extra_body) if args.extra_body else None
        return ModelTarget(
            args.target,
            args.model,
            api_key=os.environ.get(args.api_key_env, "EMPTY"),
            extra_body=extra,
            budget_chars=args.budget_chars,
        )
    from evals.runner.clients.api import ApiTarget  # noqa: PLC0415

    password = os.environ.get(args.password_env)
    if not args.username or not password:
        raise SystemExit(f"api mode needs --username and the password in ${args.password_env}")
    verify: bool | str = False if args.insecure else (args.ca_bundle or True)
    return ApiTarget(args.target, args.username, password, verify=verify)


def cmd_run(args: argparse.Namespace) -> int:
    cases = load_cases(args.cases)
    if args.category:
        cases = [c for c in cases if c.category in args.category]
    if args.only:
        cases = [c for c in cases if any(fnmatch.fnmatch(c.id, p) for p in args.only)]
    if args.limit:
        cases = cases[: args.limit]
    target = _target(args)
    judge_url = args.judge_url or (args.target if args.mode == "model" else None)
    judge = (
        Judge(judge_url, args.judge_model or args.model)
        if judge_url and not args.no_judge
        else None
    )
    try:
        results = run_cases(cases, target, judge)
    finally:
        target.close()
        if judge:
            judge.close()
    config = load_gates(Path(args.gates)) if args.gates else load_gates()
    gates = evaluate_gates(results, config)
    run = {
        "started_at": args.started_at,
        **target.describe(),
        **(judge.describe() if judge else {"judge_model": "none (summaries need overrides)"}),
        "cases": args.cases,
        "case_count": len(cases),
        "git_commit": _git_commit(),
        "corpus_seed": corpus.manifest().seed,
        "masking": corpus.masking_method(),
        "figure_check": grounding_method(),
    }
    out = write_report(results, gates, run, Path(args.out) if args.out else None)
    failed = [g for g in gates if g.enabled and g.status == "fail" and not args.no_gates]
    print(f"report: {out / 'report.md'}")
    for g in gates:
        print(f"gate {g.name}: {g.status} ({g.threshold})")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.runner",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run cases against a target and write a report")
    run.add_argument("--mode", choices=["api", "model"], required=True)
    run.add_argument("--cases", default=DEFAULT_CASES)
    run.add_argument(
        "--target", required=True, help="app base URL (api) or OpenAI base URL (model)"
    )
    run.add_argument(
        "--model", default="chat", help="served model name (model mode; default judge)"
    )
    run.add_argument("--api-key-env", default="EVAL_API_KEY")
    run.add_argument("--extra-body", help="JSON merged into model requests (default: thinking off)")
    run.add_argument("--budget-chars", type=int, default=24_000, help="model-mode excerpt budget")
    run.add_argument("--username", default=os.environ.get("EVAL_USERNAME"))
    run.add_argument("--password-env", default="EVAL_PASSWORD")
    run.add_argument(
        "--insecure", action="store_true", help="skip TLS verification (UAT internal CA)"
    )
    run.add_argument("--ca-bundle")
    run.add_argument(
        "--judge-url", help="OpenAI base URL for the summary judge (model mode: --target)"
    )
    run.add_argument("--judge-model")
    run.add_argument("--no-judge", action="store_true")
    run.add_argument("--gates", help="gates YAML (default evals/runner/gates.yaml)")
    run.add_argument("--no-gates", action="store_true", help="report gates but always exit 0")
    run.add_argument("--category", action="append")
    run.add_argument("--only", action="append", help="case id glob (repeatable)")
    run.add_argument("--limit", type=int)
    run.add_argument("--out", help="report directory (default evals/reports/<timestamp>)")
    for name in ("count", "validate"):
        p = sub.add_parser(name)
        p.add_argument("--cases", default=DEFAULT_CASES)
    sub.add_parser(
        "author", help="regenerate evals/cases/eval from the corpus (run `make corpus` first)"
    )
    args = parser.parse_args(argv)
    args.started_at = datetime.now(UTC).isoformat(timespec="seconds")

    try:
        if args.command == "run":
            return cmd_run(args)
        if args.command == "author":
            from evals.runner.authoring import author  # noqa: PLC0415

            print(f"authored {len(author())} cases")
            return 0
        cases = load_cases(args.cases)
        counts, problems = count(cases)
        for category in sorted(counts):
            minimum = MINIMUMS.get(category)
            print(f"{category:14} {counts[category]:3}" + (f"  (min {minimum})" if minimum else ""))
        print(f"{'total':14} {len(cases):3}  (min {MIN_TOTAL})")
        if args.command == "count" and problems:
            print("below minimum: " + "; ".join(problems), file=sys.stderr)
            return 1
        return 0
    except (CaseError, corpus.CorpusMissingError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
