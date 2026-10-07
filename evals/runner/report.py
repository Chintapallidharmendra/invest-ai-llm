"""Reports and gates (Story 1.8, AC #5).

Writes ``evals/reports/<timestamp>/results.json`` and ``report.md`` (git-ignored). The
report has pass rates per category and overall, plus each gate's value against its PRD
threshold.
"""

import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml  # type: ignore[import-untyped]

from evals.runner.schema import REPO_ROOT
from evals.runner.scoring import CaseResult

REPORTS_DIR: Final = REPO_ROOT / "evals" / "reports"
GATES_FILE: Final = Path(__file__).with_name("gates.yaml")
SCORED: Final = {"passed", "failed", "error", "needs_review"}


@dataclass
class GateResult:
    name: str
    enabled: bool
    status: str  # pass | fail | n/a
    value: float | None
    threshold: str
    detail: str = ""


def category_stats(results: list[CaseResult]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for category in sorted({r.category for r in results}):
        rows = [r for r in results if r.category == category]
        counts = Counter(r.status for r in rows)
        scored = sum(counts[s] for s in SCORED)
        stats[category] = {
            "total": len(rows),
            **{s: counts[s] for s in ("passed", "failed", "skipped", "error", "needs_review")},
            "pass_rate": counts["passed"] / scored if scored else None,
        }
    return stats


def evaluate_gates(results: list[CaseResult], config: dict[str, Any]) -> list[GateResult]:
    gates = config.get("gates", {})
    scored = [r for r in results if r.status in SCORED]
    out = []

    g = gates.get("overall_pass_rate", {})
    rate = sum(r.status == "passed" for r in scored) / len(scored) if scored else None
    out.append(
        _gate(
            "overall_pass_rate",
            g,
            value=rate,
            threshold=f">= {g.get('min', 0.85):.0%}",
            ok=None if rate is None else rate >= g.get("min", 0.85),
            detail=f"{len(scored)} scored cases",
        )
    )

    g = gates.get("summary_quality", {})
    summaries = [r for r in scored if r.category == "summary"]
    good = [r for r in summaries if (r.summary_score or 0) >= g.get("min_score", 4)]
    frac = len(good) / len(summaries) if summaries else None
    out.append(
        _gate(
            "summary_quality",
            g,
            value=frac,
            threshold=(
                f">= {g.get('min_fraction', 0.85):.0%} of summaries"
                f" score >= {g.get('min_score', 4)}/5"
            ),
            ok=None if frac is None else frac >= g.get("min_fraction", 0.85),
            detail=f"{len(good)}/{len(summaries)} summaries",
        )
    )

    g = gates.get("ungrounded_figures", {})
    total = sum(len(r.ungrounded_figures) for r in scored)
    out.append(
        _gate(
            "ungrounded_figures",
            g,
            value=float(total) if scored else None,
            threshold=f"<= {g.get('max', 0)}",
            ok=None if not scored else total <= g.get("max", 0),
            detail=", ".join(
                f"{r.case_id}: {r.ungrounded_figures[:3]}" for r in scored if r.ungrounded_figures
            )[:500],
        )
    )

    g = gates.get("excel_reference_match", {})
    ops = [r for r in scored if r.category == "excel_op"]
    match = sum(bool(r.reference_match) for r in ops) / len(ops) if ops else None
    out.append(
        _gate(
            "excel_reference_match",
            g,
            value=match,
            threshold=f">= {g.get('min', 1.0):.0%}",
            ok=None if match is None else match >= g.get("min", 1.0),
            detail=f"{len(ops)} applied operations",
        )
    )
    return out


def _gate(
    name: str,
    cfg: dict[str, Any],
    *,
    value: float | None,
    threshold: str,
    ok: bool | None,
    detail: str,
) -> GateResult:
    status = "n/a" if ok is None else ("pass" if ok else "fail")
    return GateResult(name, bool(cfg.get("enabled", True)), status, value, threshold, detail)


def load_gates(path: Path = GATES_FILE) -> dict[str, Any]:
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def write_report(
    results: list[CaseResult],
    gates: list[GateResult],
    run: dict[str, Any],
    out_dir: Path | None = None,
) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory = out_dir or REPORTS_DIR / stamp
    directory.mkdir(parents=True, exist_ok=True)
    stats = category_stats(results)
    scored = [r for r in results if r.status in SCORED]
    overall = {
        "total": len(results),
        "scored": len(scored),
        "passed": sum(r.status == "passed" for r in results),
        "skipped": sum(r.status == "skipped" for r in results),
        "pass_rate": (sum(r.status == "passed" for r in scored) / len(scored)) if scored else None,
    }
    payload = {
        "run": {**run, "finished_at": datetime.now(UTC).isoformat(timespec="seconds")},
        "overall": overall,
        "categories": stats,
        "gates": [asdict(g) for g in gates],
        "cases": [asdict(r) for r in results],
    }
    (directory / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    (directory / "report.md").write_text(_markdown(payload, results, gates), encoding="utf-8")
    return directory


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _markdown(payload: dict[str, Any], results: list[CaseResult], gates: list[GateResult]) -> str:
    run, overall = payload["run"], payload["overall"]
    lines = [
        "# Eval report",
        "",
        "Synthetic corpus only. Never attach production content to eval reports.",
        "",
        *[f"- **{k.replace('_', ' ')}:** {v}" for k, v in run.items()],
        "",
        f"**Overall:** {overall['passed']}/{overall['scored']} scored cases passed "
        f"({_pct(overall['pass_rate'])}); {overall['skipped']} skipped of {overall['total']}.",
        "",
        "## Gates",
        "",
        "| Gate | Enabled | Status | Value | Threshold | Detail |",
        "|---|---|---|---|---|---|",
    ]
    for g in gates:
        value = (
            "n/a"
            if g.value is None
            else (
                f"{g.value:.1%}"
                if g.value <= 1 and g.name != "ungrounded_figures"
                else f"{g.value:g}"
            )
        )
        enabled = "yes" if g.enabled else "no"
        detail = g.detail.replace("|", "/")
        lines.append(
            f"| {g.name} | {enabled} | **{g.status}** | {value} | {g.threshold} | {detail} |"
        )
    lines += [
        "",
        "## Categories",
        "",
        "| Category | Total | Passed | Failed | Skipped | Errors | Review | Pass rate |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for category, s in payload["categories"].items():
        lines.append(
            f"| {category} | {s['total']} | {s['passed']} | {s['failed']} | {s['skipped']} | "
            f"{s['error']} | {s['needs_review']} | {_pct(s['pass_rate'])} |"
        )
    lines += [
        "",
        "## Cases not passed",
        "",
        "| Case | Category | Status | Reason |",
        "|---|---|---|---|",
    ]
    for r in results:
        if r.status == "passed":
            continue
        reason = (
            r.skipped_reason
            or r.error
            or "; ".join(f"{c.name}: {c.detail}" for c in r.failed_checks)
        )
        if r.override:
            reason += f" (override by {r.override.get('reviewer', '?')})"
        lines.append(
            f"| {r.case_id} | {r.category} | {r.status} | {reason.replace('|', '/')[:300]} |"
        )
    return "\n".join(lines) + "\n"
