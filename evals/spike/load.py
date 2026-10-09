"""Concurrent streaming load, background summary calls and the soak (AC #3).

- :func:`run_level` runs ``concurrency`` chat streams in parallel for ``rounds`` each
  and reports TTFT p50/p95 and tokens/s per stream.
- :func:`summary_loop` keeps one background map call going at a time (low priority,
  long prompt), counting completions, so chat is measured with summaries alongside.
- :func:`soak` runs ``chats`` streams + ``summaries`` loops for ``duration_s``, samples
  ``nvidia-smi`` and vLLM ``/metrics``, and counts errors and OOM signs.

Timing maths (percentiles, rates) are pure functions so they can be checked against
synthetic distributions.
"""

import asyncio
import contextlib
import math
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Final

import httpx

from evals.spike.client import Endpoint, chat_body, metric_sum, metrics, stream_chat

CHAT_PRIORITY: Final = 0  # vLLM: lower value = scheduled first (ADR-029)
BACKGROUND_PRIORITY: Final = 10

CHAT_PROMPT: Final = (
    "Using the excerpt, answer in three short bullet points with page citations. "
    "Excerpt (Nilgiri Logistics IM, p.12): Revenue from operations rose to Rs 1,250.0 crore "
    "in FY25 from Rs 1,082.3 crore in FY24, driven by contract logistics in South India. "
    "EBITDA was Rs 265.0 crore. Question: summarise the growth drivers."
)
# A summary map call: a long synthetic section (~5k tokens) and a bounded output.
MAP_SECTION: Final = " ".join(
    f"Section {i}: the company operates {i + 3} warehouses and {i * 7 + 11} trucks; "
    f"revenue in segment {i} grew {i % 9 + 2}% year on year with stable margins."
    for i in range(160)
)


# --- Pure maths --------------------------------------------------------------------------


def percentile(values: Sequence[float], p: float) -> float:
    """Linear-interpolated percentile (numpy's default method); NaN when empty."""
    if not values:
        return math.nan
    if not 0 <= p <= 100:  # noqa: PLR2004
        raise ValueError("p must be within 0-100")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * p / 100
    lo, hi = math.floor(rank), math.ceil(rank)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


@dataclass(frozen=True)
class StreamResult:
    ttft_s: float | None  # time to the first content token; None if none arrived
    total_s: float
    completion_tokens: int
    error: str | None = None

    @property
    def tokens_per_s(self) -> float | None:
        """Decode speed after the first token (the user-visible streaming rate)."""
        if self.ttft_s is None or self.completion_tokens < 2:  # noqa: PLR2004
            return None
        decode = self.total_s - self.ttft_s
        return (self.completion_tokens - 1) / decode if decode > 0 else None


def summarise_streams(results: Sequence[StreamResult]) -> dict[str, Any]:
    ok = [r for r in results if r.error is None and r.ttft_s is not None]
    ttfts = [r.ttft_s for r in ok if r.ttft_s is not None]
    rates = [rate for r in ok if (rate := r.tokens_per_s) is not None]
    return {
        "requests": len(results),
        "errors": len(results) - len(ok),
        "error_kinds": sorted({r.error for r in results if r.error}),
        "ttft_p50_s": percentile(ttfts, 50),
        "ttft_p95_s": percentile(ttfts, 95),
        "tokens_per_s_p50": percentile(rates, 50),
        "tokens_per_s_p5": percentile(rates, 5),  # the slowest streams
    }


def per_minute(count: int, seconds: float) -> float:
    return count * 60 / seconds if seconds > 0 else 0.0


# --- Requests ------------------------------------------------------------------------------


async def stream_once(
    client: httpx.AsyncClient,
    endpoint: Endpoint,
    prompt: str,
    *,
    max_tokens: int = 256,
    priority: int | None = CHAT_PRIORITY,
    cache_salt: str | None = None,
) -> StreamResult:
    body = chat_body(
        endpoint,
        [{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        stream=True,
        priority=priority,
        cache_salt=cache_salt,
    )
    started = time.perf_counter()
    first: float | None = None
    pieces = 0
    usage_tokens: int | None = None
    try:
        async for event in stream_chat(client, endpoint, body):
            usage = event.data.get("usage")
            if usage:
                usage_tokens = int(usage.get("completion_tokens") or 0)
            for choice in event.data.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content") or delta.get("tool_calls"):
                    pieces += 1
                    if first is None:
                        first = event.at
    except (httpx.HTTPError, ValueError) as exc:
        return StreamResult(None, time.perf_counter() - started, 0, _error_kind(exc))
    total = time.perf_counter() - started
    tokens = usage_tokens if usage_tokens is not None else pieces
    return StreamResult(None if first is None else first - started, total, tokens)


def _error_kind(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    return type(exc).__name__


async def run_level(
    endpoint: Endpoint,
    concurrency: int,
    *,
    rounds: int = 3,
    max_tokens: int = 256,
    prompt: str = CHAT_PROMPT,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """``concurrency`` parallel users, each sending ``rounds`` chats back to back."""
    async with _client(client) as http:

        async def user(n: int) -> list[StreamResult]:
            return [
                await stream_once(
                    http, endpoint, f"{prompt} (user {n}, turn {r})", max_tokens=max_tokens
                )
                for r in range(rounds)
            ]

        started = time.perf_counter()
        batches = await asyncio.gather(*(user(n) for n in range(concurrency)))
        elapsed = time.perf_counter() - started
    results = [r for batch in batches for r in batch]
    return {"concurrency": concurrency, "elapsed_s": elapsed, **summarise_streams(results)}


@dataclass
class LoopStats:
    completed: int = 0
    errors: int = 0
    latencies_s: list[float] = field(default_factory=list)


async def summary_loop(
    endpoint: Endpoint,
    stop: asyncio.Event,
    stats: LoopStats,
    *,
    max_tokens: int = 300,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Back-to-back background map calls until ``stop`` is set."""
    async with _client(client) as http:
        while not stop.is_set():
            result = await stream_once(
                http,
                endpoint,
                f"Summarise the key operating facts in five bullets.\n\n{MAP_SECTION}",
                max_tokens=max_tokens,
                priority=BACKGROUND_PRIORITY,
            )
            if result.error is None:
                stats.completed += 1
                stats.latencies_s.append(result.total_s)
            else:
                stats.errors += 1
                await asyncio.sleep(1)


async def chat_with_summaries(
    endpoint: Endpoint,
    *,
    chats: int,
    summaries: int,
    rounds: int = 3,
    max_tokens: int = 256,
) -> dict[str, Any]:
    """A :func:`run_level` measurement while ``summaries`` map loops run alongside."""
    stop = asyncio.Event()
    stats = LoopStats()
    async with httpx.AsyncClient(timeout=300) as http:
        loops = [
            asyncio.create_task(summary_loop(endpoint, stop, stats, client=http))
            for _ in range(summaries)
        ]
        started = time.perf_counter()
        try:
            chat = await run_level(
                endpoint, chats, rounds=rounds, max_tokens=max_tokens, client=http
            )
        finally:
            stop.set()
            await asyncio.gather(*loops, return_exceptions=True)
        elapsed = time.perf_counter() - started
    return {
        "chat": chat,
        "summaries": {
            "loops": summaries,
            "completed": stats.completed,
            "errors": stats.errors,
            "map_calls_per_min": per_minute(stats.completed, elapsed),
            "latency_p50_s": percentile(stats.latencies_s, 50),
        },
    }


# --- Soak ----------------------------------------------------------------------------------


GPU_QUERY: Final = (
    "memory.used,memory.total,clocks.sm,clocks.max.sm,temperature.gpu,utilization.gpu"
)


def parse_nvidia_smi(output: str) -> dict[str, float] | None:
    """One ``--format=csv,noheader,nounits`` line of :data:`GPU_QUERY`."""
    line = output.strip().splitlines()[0] if output.strip() else ""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) != len(GPU_QUERY.split(",")):
        return None
    try:
        values = [float(p) for p in parts]
    except ValueError:
        return None
    return dict(zip(("mem_used_mib", "mem_total_mib", "sm_clock_mhz", "sm_clock_max_mhz",
                     "temp_c", "util_pct"), values, strict=True))  # fmt: skip


def sample_gpu() -> dict[str, float] | None:
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603 (fixed argv)
            [smi, f"--query-gpu={GPU_QUERY}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_nvidia_smi(out)


# vLLM v1 metric names (older names as fallbacks).
PREEMPTIONS: Final = ("vllm:num_preemptions_total", "vllm:num_preemptions")
KV_USAGE: Final = ("vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc")


def first_metric(values: dict[str, float], names: Sequence[str]) -> float | None:
    for name in names:
        if any(k == name or k.startswith(name + "{") for k in values):
            return metric_sum(values, name)
    return None


def summarise_soak(
    *,
    chat: Sequence[StreamResult],
    gpu: Sequence[dict[str, float]],
    kv_usage: Sequence[float],
    preemptions: tuple[float | None, float | None],
    summaries: LoopStats,
    duration_s: float,
) -> dict[str, Any]:
    errors = [r.error for r in chat if r.error] + ["summary"] * summaries.errors
    oom_signs = [
        e for e in errors if e in {"http_500", "http_503", "RemoteProtocolError", "ConnectError"}
    ]
    before, after = preemptions
    mem = [s["mem_used_mib"] for s in gpu]
    total = gpu[0]["mem_total_mib"] if gpu else None
    clocks = [s["sm_clock_mhz"] / s["sm_clock_max_mhz"] for s in gpu if s.get("sm_clock_max_mhz")]
    return {
        "duration_s": duration_s,
        "chat": summarise_streams(chat),
        "summaries": {
            "completed": summaries.completed,
            "errors": summaries.errors,
            "map_calls_per_min": per_minute(summaries.completed, duration_s),
        },
        "errors": len(errors),
        "oom_signs": len(oom_signs),
        "preemptions": None if before is None or after is None else after - before,
        "kv_usage_max": max(kv_usage) if kv_usage else None,
        "gpu": {
            "samples": len(gpu),
            "mem_used_max_mib": max(mem) if mem else None,
            "headroom_min_mib": (total - max(mem)) if mem and total else None,
            "sm_clock_ratio_min": min(clocks) if clocks else None,  # vGPU throttling
            "temp_max_c": max((s["temp_c"] for s in gpu), default=None),
            "temp_first_last_c": [gpu[0]["temp_c"], gpu[-1]["temp_c"]] if gpu else None,
        },
        "passed": not errors and not oom_signs,
    }


async def soak(
    endpoint: Endpoint,
    *,
    duration_s: float = 1800,
    chats: int = 10,
    summaries: int = 2,
    sample_every_s: float = 15,
    max_tokens: int = 256,
    gpu_sampler: Callable[[], dict[str, float] | None] = sample_gpu,
) -> dict[str, Any]:
    """``chats`` users chatting continuously and ``summaries`` map loops, for ``duration_s``."""
    stop = asyncio.Event()
    loop_stats = LoopStats()
    chat_results: list[StreamResult] = []
    gpu_samples: list[dict[str, float]] = []
    kv_usage: list[float] = []

    async with httpx.AsyncClient(timeout=300) as http:
        preempt_before = first_metric(await metrics(http, endpoint), PREEMPTIONS)

        async def chatter(n: int) -> None:
            turn = 0
            while not stop.is_set():
                result = await stream_once(
                    http, endpoint, f"{CHAT_PROMPT} (user {n}, turn {turn})", max_tokens=max_tokens
                )
                chat_results.append(result)
                turn += 1
                if result.error:
                    await asyncio.sleep(1)

        async def sampler() -> None:
            while not stop.is_set():
                sample = gpu_sampler()
                if sample:
                    gpu_samples.append(sample)
                kv = first_metric(await metrics(http, endpoint), KV_USAGE)
                if kv is not None:
                    kv_usage.append(kv)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=sample_every_s)

        started = time.perf_counter()
        tasks = [asyncio.create_task(chatter(n)) for n in range(chats)]
        tasks += [
            asyncio.create_task(summary_loop(endpoint, stop, loop_stats, client=http))
            for _ in range(summaries)
        ]
        tasks.append(asyncio.create_task(sampler()))
        try:
            await asyncio.sleep(duration_s)
        finally:
            stop.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        elapsed = time.perf_counter() - started
        preempt_after = first_metric(await metrics(http, endpoint), PREEMPTIONS)

    return summarise_soak(
        chat=chat_results,
        gpu=gpu_samples,
        kv_usage=kv_usage,
        preemptions=(preempt_before, preempt_after),
        summaries=loop_stats,
        duration_s=elapsed,
    )


@contextlib.asynccontextmanager
async def _client(client: httpx.AsyncClient | None):  # type: ignore[no-untyped-def]
    if client is not None:
        yield client
        return
    async with httpx.AsyncClient(timeout=300) as http:
        yield http


def result_dict(result: StreamResult) -> dict[str, Any]:
    return {**asdict(result), "tokens_per_s": result.tokens_per_s}
