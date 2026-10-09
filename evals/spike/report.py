"""Render ``REPORT.md`` from the spike's ``results.json`` (AC #5).

``results.json`` is built up by the CLI, one candidate at a time::

    {"spike": {...run facts...},
     "candidates": [{"name", "quantisation", "started", "start_error", "max_model_len",
                     "max_num_seqs", "quality", "tool_calls", "performance", "soak",
                     "features"}, ...]}

Every section is optional, so a candidate that failed to start (or a run cut short)
still renders. The decision itself is written by a person, in the decision log.
"""

import math
from collections.abc import Mapping, Sequence
from typing import Any, Final

# PRD targets the spike confirms or proposes to change († thresholds).
TARGETS: Final = [
    ("NFR-001", "TTFT p95, concept question, 10 chats + 2 summaries", "<= 3 s", "ttft_p95_s", 3.0, "max"),
    ("NFR-001", "TTFT p95, document question, 10 chats + 2 summaries", "<= 8 s", "ttft_p95_s", 8.0, "max"),
    ("NFR-002", "Streaming rate per stream at 10 streams (p50)", ">= 15 tok/s", "tokens_per_s_p50", 15.0, "min"),
    ("NFR-004", "30-minute soak: errors / OOM", "0 / 0", None, None, None),
    ("NFR-010", "Eval pass rate (model mode)", ">= 85%", None, 0.85, "min"),
]  # fmt: skip


def _num(value: Any, fmt: str = "{:.2f}") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return fmt.format(value)


def _pct(value: Any) -> str:
    return _num(value, "{:.1%}")


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def _alongside(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    busy: Mapping[str, Any] = (candidate.get("performance") or {}).get("with_summaries") or {}
    return busy


def overview(candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    rows = []
    for c in candidates:
        quality = (c.get("quality") or {}).get("overall") or {}
        tools = (c.get("tool_calls") or {}).get("summary") or {}
        busy = _alongside(c).get("chat") or {}
        soak = c.get("soak") or {}
        rows.append(
            [
                c["name"],
                c.get("quantisation", "?"),
                "yes" if c.get("started") else f"no ({c.get('start_error') or 'failed'})",
                _pct(quality.get("pass_rate")),
                _pct(tools.get("right_args_rate")),
                _num(busy.get("ttft_p95_s")),
                _num(busy.get("tokens_per_s_p50"), "{:.1f}"),
                ("pass" if soak.get("passed") else "fail") if soak else "n/a",
            ]
        )
    header = ["Candidate", "Quant", "Started", "Eval pass", "Tool args right",
              "TTFT p95 @10+2 (s)", "tok/s p50 @10+2", "30-min soak"]  # fmt: skip
    return _table(header, rows)


def candidate_section(c: Mapping[str, Any]) -> list[str]:
    out = [f"### {c['name']}", ""]
    out.append(
        f"Quantisation {c.get('quantisation', '?')} · max model len {c.get('max_model_len', '?')} "
        f"· max seqs {c.get('max_num_seqs', '?')}"
    )
    out.append("")
    if not c.get("started"):
        out += [f"Did not start: {c.get('start_error') or 'unknown error'}.", ""]
        return out

    quality = c.get("quality") or {}
    if quality.get("categories"):
        out += ["**Quality (eval runner, model mode)**", ""]
        rows = [
            [name, str(s.get("passed", 0)), str(s.get("total", 0)), _pct(s.get("pass_rate"))]
            for name, s in sorted(quality["categories"].items())
        ]
        out += [*_table(["Category", "Passed", "Total", "Pass rate"], rows), ""]

    tools = (c.get("tool_calls") or {}).get("summary")
    if tools:
        out += ["**Tool calls**", ""]
        out += _table(
            ["Prompts", "Valid call", "Right tool", "Right arguments", "Failures"],
            [[str(tools["total"]), _pct(tools.get("valid_rate")), _pct(tools.get("right_tool_rate")),
              _pct(tools.get("right_args_rate")),
              ", ".join(f"{k} {v}" for k, v in sorted((tools.get("failures") or {}).items())) or "none"]],
        )  # fmt: skip
        out.append("")

    levels = (c.get("performance") or {}).get("levels") or []
    if levels:
        out += ["**Performance (chat only)**", ""]
        rows = [
            [str(lv["concurrency"]), _num(lv.get("ttft_p50_s")), _num(lv.get("ttft_p95_s")),
             _num(lv.get("tokens_per_s_p50"), "{:.1f}"), _num(lv.get("tokens_per_s_p5"), "{:.1f}"),
             str(lv.get("errors", 0))]
            for lv in levels
        ]  # fmt: skip
        out += [
            *_table(
                ["Streams", "TTFT p50 (s)", "TTFT p95 (s)", "tok/s p50", "tok/s p5", "Errors"], rows
            ),
            "",
        ]

    busy = _alongside(c)
    if busy:
        chat, summaries = busy.get("chat") or {}, busy.get("summaries") or {}
        out += ["**Chat with background summaries**", ""]
        out += _table(
            ["Chats", "Summary loops", "TTFT p95 (s)", "tok/s p50", "Map calls/min", "Errors"],
            [[str(chat.get("concurrency", "?")), str(summaries.get("loops", "?")),
              _num(chat.get("ttft_p95_s")), _num(chat.get("tokens_per_s_p50"), "{:.1f}"),
              _num(summaries.get("map_calls_per_min"), "{:.1f}"),
              str((chat.get("errors") or 0) + (summaries.get("errors") or 0))]],
        )  # fmt: skip
        out.append("")

    soak = c.get("soak")
    if soak:
        gpu = soak.get("gpu") or {}
        out += ["**Soak**", ""]
        out += _table(
            ["Minutes", "Errors", "OOM signs", "Preemptions", "KV max", "GPU mem max (MiB)",
             "Headroom min (MiB)", "SM clock min", "Temp first/last (C)"],
            [[_num(soak.get("duration_s", 0) / 60, "{:.0f}"), str(soak.get("errors")), str(soak.get("oom_signs")),
              _num(soak.get("preemptions"), "{:.0f}"), _pct(soak.get("kv_usage_max")),
              _num(gpu.get("mem_used_max_mib"), "{:.0f}"), _num(gpu.get("headroom_min_mib"), "{:.0f}"),
              _pct(gpu.get("sm_clock_ratio_min")),
              "/".join(_num(t, "{:.0f}") for t in (gpu.get("temp_first_last_c") or [])) or "n/a"]],
        )  # fmt: skip
        out.append("")

    features = c.get("features") or {}
    if features:
        out += ["**Platform features**", ""]
        rows = [[name, f["verdict"], f.get("detail", "")] for name, f in features.items()]
        out += [*_table(["Probe", "Verdict", "Detail"], rows), ""]
    return out


def thresholds(candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    rows = []
    for nfr, what, target, key, _, _ in TARGETS:
        measured = []
        for c in candidates:
            if not c.get("started"):
                continue
            busy = _alongside(c).get("chat") or {}
            if key is not None:
                value = busy.get(key)
            elif nfr == "NFR-004":
                soak = c.get("soak") or {}
                value = (
                    f"{soak.get('errors', 'n/a')} / {soak.get('oom_signs', 'n/a')}"
                    if soak
                    else None
                )
            else:
                value = ((c.get("quality") or {}).get("overall") or {}).get("pass_rate")
            text = (
                value
                if isinstance(value, str)
                else (_pct(value) if nfr == "NFR-010" else _num(value))
            )
            measured.append(f"{c['name']}: {text}")
        rows.append([nfr, what, target, "; ".join(measured) or "n/a"])
    return _table(["NFR", "Measure", "Target", "Measured"], rows)


def render(results: Mapping[str, Any]) -> str:
    spike = results.get("spike") or {}
    candidates = results.get("candidates") or []
    out = [
        "# Model selection spike (Story 1.9)",
        "",
        f"Run on {spike.get('host', '?')} ({spike.get('gpu', '?')}) on {spike.get('date', '?')}, "
        f"vLLM `{spike.get('vllm_image', '?')}`, commit `{spike.get('git_commit', '?')}`. "
        f"Guard co-resident: {spike.get('guard', '?')} (GPU split {spike.get('gpu_split', '?')}). "
        "Synthetic corpus only.",
        "",
        "## Overview",
        "",
        *overview(candidates),
        "",
        "## Thresholds (†)",
        "",
        *thresholds(candidates),
        "",
        "TTFT for concept and document questions is measured with the same chat load; the",
        "document-question figure applies when retrieval adds its context (Story 3.2).",
        "",
        "## Candidates",
        "",
    ]
    for candidate in candidates:
        out += candidate_section(candidate)
    out += [
        "## Decision",
        "",
        spike.get("decision")
        or "_To be written after review: model, quantisation, vLLM tag, context length, "
        "max sequences, and the threshold changes proposed (see decision-log.md)._",
        "",
    ]
    return "\n".join(out)
