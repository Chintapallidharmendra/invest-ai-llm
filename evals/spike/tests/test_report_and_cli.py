"""AC #5: the report renders from a sample results.json; the CLI builds results.json."""

import json
from pathlib import Path

from evals.spike import cli, report
from tests.fakes.fake_llm import FakeLLM, Text

SAMPLE = Path(__file__).with_name("sample_results.json")


def test_report_renders_from_the_sample() -> None:
    text = report.render(json.loads(SAMPLE.read_text()))
    assert text.startswith("# Model selection spike (Story 1.9)")
    assert "| Qwen3-14B-AWQ | AWQ INT4 | yes | 87.5% | 88.6% | 2.60 | 17.2 | pass |" in text
    assert "no (MXFP4 kernels need compute capability >= 9.0)" in text
    assert "Did not start: MXFP4 kernels" in text
    assert (
        "| NFR-002 | Streaming rate per stream at 10 streams (p50) | >= 15 tok/s | Qwen3-14B-AWQ: 2.60"
        not in text
    )
    assert "Qwen3-14B-AWQ: 17.20" in text  # NFR-002 measured with summaries alongside
    assert "Qwen3-14B-AWQ: 0 / 0" in text  # NFR-004
    assert "Qwen3-14B-AWQ: 87.5%" in text  # NFR-010
    assert "| cache_salt | honoured |" in text
    assert "| 30 | 0 | 0 | 0 | 91.0% | 22100 | 2476 | 97.0% | 61/72 |" in text
    assert "## Decision" in text


def test_report_renders_with_nothing_measured() -> None:
    text = report.render({"spike": {}, "candidates": [{"name": "X", "started": True}]})
    assert "| X | ? | yes | n/a | n/a | n/a | n/a | n/a |" in text


def test_cli_builds_results_and_report(tmp_path: Path, fake_llm: FakeLLM) -> None:
    results, out = tmp_path / "results.json", tmp_path / "REPORT.md"
    assert (
        cli.main(["start", "--results", str(results), "--candidate", "Fake-7B", "--quant", "none"])
        == 0
    )
    eval_results = tmp_path / "eval.json"
    eval_results.write_text(
        json.dumps({"overall": {"pass_rate": 0.9}, "categories": {}, "cases": [1]})
    )
    assert cli.main(["quality", "--results", str(results), "--candidate", "Fake-7B",
                     "--eval-results", str(eval_results)]) == 0  # fmt: skip
    fake_llm.add(pattern="user", reply=Text("fine " * 10))
    assert cli.main(["load", "--results", str(results), "--candidate", "Fake-7B", "--target",
                     fake_llm.base_url, "--levels", "1,2", "--rounds", "1"]) == 0  # fmt: skip
    assert (
        cli.main(["report", "--results", str(results), "--out", str(out), "--host", "laptop"]) == 0
    )

    data = json.loads(results.read_text())
    [entry] = data["candidates"]
    assert entry["started"] is True
    assert "cases" not in entry["quality"]  # transcripts stay out of the committed report
    assert [lv["concurrency"] for lv in entry["performance"]["levels"]] == [1, 2]
    assert data["spike"]["host"] == "laptop"
    assert "Fake-7B" in out.read_text()
