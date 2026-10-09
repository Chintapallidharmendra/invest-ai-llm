"""AC #3: the load generator's timing maths, checked against synthetic distributions,
and short runs against the fake LLM."""

import math
import random

import pytest

from evals.spike import load
from evals.spike.client import Endpoint, parse_prometheus
from evals.spike.load import LoopStats, StreamResult, percentile, summarise_soak, summarise_streams
from tests.fakes.fake_llm import Error, FakeLLM, Text


def test_percentile_matches_linear_interpolation() -> None:
    values = list(range(1, 101))  # 1..100
    assert percentile(values, 50) == pytest.approx(50.5)
    assert percentile(values, 95) == pytest.approx(95.05)
    assert percentile(values, 0) == 1
    assert percentile(values, 100) == 100
    assert percentile([3.0], 95) == 3.0
    assert math.isnan(percentile([], 50))
    with pytest.raises(ValueError, match="0-100"):
        percentile(values, 101)


def test_percentiles_of_a_synthetic_latency_distribution() -> None:
    rng = random.Random(19)  # noqa: S311 (test data)
    # Log-normal TTFTs, median 0.8 s: p95 of a large sample is ~ 0.8 * e^(1.645 * 0.5).
    samples = [rng.lognormvariate(math.log(0.8), 0.5) for _ in range(20_000)]
    assert percentile(samples, 50) == pytest.approx(0.8, rel=0.03)
    assert percentile(samples, 95) == pytest.approx(0.8 * math.exp(1.645 * 0.5), rel=0.04)


def test_tokens_per_second_excludes_the_first_token() -> None:
    result = StreamResult(ttft_s=1.0, total_s=3.0, completion_tokens=41)
    assert result.tokens_per_s == pytest.approx(20.0)  # 40 tokens over 2 s of decode
    assert StreamResult(None, 1.0, 0, "http_500").tokens_per_s is None
    assert StreamResult(0.5, 0.5, 1).tokens_per_s is None


def test_summarise_streams_counts_errors() -> None:
    results = [StreamResult(0.1 * i, 0.1 * i + 2, 41) for i in range(1, 11)]
    results.append(StreamResult(None, 5.0, 0, "ReadTimeout"))
    summary = summarise_streams(results)
    assert (summary["requests"], summary["errors"]) == (11, 1)
    assert summary["error_kinds"] == ["ReadTimeout"]
    assert summary["ttft_p50_s"] == pytest.approx(0.55)
    assert summary["tokens_per_s_p50"] == pytest.approx(20.0)


def test_parse_nvidia_smi() -> None:
    sample = load.parse_nvidia_smi("21345, 24576, 1695, 1695, 71, 98\n")
    assert sample == {"mem_used_mib": 21345.0, "mem_total_mib": 24576.0, "sm_clock_mhz": 1695.0,
                      "sm_clock_max_mhz": 1695.0, "temp_c": 71.0, "util_pct": 98.0}  # fmt: skip
    assert load.parse_nvidia_smi("[N/A], 1") is None
    assert load.parse_nvidia_smi("") is None


def test_summarise_soak() -> None:
    chat = [StreamResult(0.4, 2.0, 41)] * 50
    gpu = [{"mem_used_mib": 20000.0, "mem_total_mib": 24576.0, "sm_clock_mhz": 1500.0,
            "sm_clock_max_mhz": 1695.0, "temp_c": t, "util_pct": 95.0} for t in (60.0, 74.0, 72.0)]  # fmt: skip
    summary = summarise_soak(chat=chat, gpu=gpu, kv_usage=[0.5, 0.92], preemptions=(3.0, 3.0),
                             summaries=LoopStats(completed=60), duration_s=1800)  # fmt: skip
    assert summary["passed"] is True
    assert summary["preemptions"] == 0
    assert summary["gpu"]["headroom_min_mib"] == 4576.0
    assert summary["gpu"]["sm_clock_ratio_min"] == pytest.approx(1500 / 1695)
    assert summary["gpu"]["temp_first_last_c"] == [60.0, 72.0]
    assert summary["summaries"]["map_calls_per_min"] == 2.0
    failed = summarise_soak(chat=[*chat, StreamResult(None, 1.0, 0, "http_500")], gpu=gpu,
                            kv_usage=[], preemptions=(None, None),
                            summaries=LoopStats(errors=1), duration_s=1800)  # fmt: skip
    assert (failed["passed"], failed["errors"], failed["oom_signs"]) == (False, 2, 1)
    assert failed["preemptions"] is None


def test_parse_prometheus() -> None:
    text = (
        "# HELP vllm:num_preemptions_total x\n"
        'vllm:num_preemptions_total{model_name="chat"} 4.0\n'
        'vllm:kv_cache_usage_perc{model_name="chat"} 0.42\n'
        "garbage line\n"
    )
    values = parse_prometheus(text)
    assert load.first_metric(values, load.PREEMPTIONS) == 4.0
    assert load.first_metric(values, load.KV_USAGE) == 0.42
    assert load.first_metric(values, ("vllm:absent",)) is None


async def test_run_level_against_the_fake(fake_llm: FakeLLM) -> None:
    fake_llm.add(
        pattern="user", reply=Text("word " * 40), delay_s=0.05, chunk_delay_s=0.002, chunk_size=5
    )
    endpoint = Endpoint(fake_llm.base_url)
    result = await load.run_level(endpoint, 5, rounds=2, max_tokens=64)
    assert (result["concurrency"], result["requests"], result["errors"]) == (5, 10, 0)
    assert 0.05 <= result["ttft_p50_s"] < 1.0
    assert result["tokens_per_s_p50"] > 0
    bodies = [r.body for r in fake_llm.chat_requests]
    assert all(b["stream"] and b["priority"] == load.CHAT_PRIORITY for b in bodies)
    assert all(b["stream_options"] == {"include_usage": True} for b in bodies)


async def test_chat_with_summaries_against_the_fake(fake_llm: FakeLLM) -> None:
    fake_llm.add(pattern="Summarise the key operating facts", reply=Text("- a\n- b"), delay_s=0.02)
    fake_llm.add(pattern="user", reply=Text("ok " * 10), delay_s=0.05)
    result = await load.chat_with_summaries(
        Endpoint(fake_llm.base_url), chats=3, summaries=2, rounds=2
    )
    assert result["chat"]["requests"] == 6
    assert result["summaries"]["completed"] >= 2
    priorities = {r.body["priority"] for r in fake_llm.chat_requests}
    assert priorities == {load.CHAT_PRIORITY, load.BACKGROUND_PRIORITY}


async def test_short_soak_against_the_fake(fake_llm: FakeLLM) -> None:
    fake_llm.add(pattern=".", reply=Text("fine " * 5), delay_s=0.01)
    samples = iter([{"mem_used_mib": 1.0, "mem_total_mib": 2.0, "sm_clock_mhz": 1.0,
                     "sm_clock_max_mhz": 1.0, "temp_c": 40.0, "util_pct": 1.0}] * 100)  # fmt: skip
    result = await load.soak(Endpoint(fake_llm.base_url), duration_s=0.6, chats=2, summaries=1,
                             sample_every_s=0.2, gpu_sampler=lambda: next(samples))  # fmt: skip
    assert result["passed"] is True
    assert result["chat"]["requests"] > 2
    assert result["gpu"]["samples"] >= 2


async def test_errors_are_counted_not_raised(fake_llm: FakeLLM) -> None:
    fake_llm.add(pattern="user", reply=Error(500, "boom"))
    result = await load.run_level(Endpoint(fake_llm.base_url), 2, rounds=1)
    assert result["errors"] == 2
    assert result["error_kinds"] == ["http_500"]
