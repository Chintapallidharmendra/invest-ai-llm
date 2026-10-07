"""AC #3 and #5: API mode against stub servers, model mode against the fake LLM,
the judge with overrides, reports and gates."""

import io
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from openpyxl import Workbook  # type: ignore[import-untyped]

from evals.runner import cli, corpus, judge
from evals.runner.clients.api import ApiTarget
from evals.runner.clients.model import ModelTarget
from evals.runner.report import evaluate_gates, load_gates, write_report
from evals.runner.schema import Case, load_cases
from evals.runner.scoring import CaseResult
from tests.fakes.fake_llm import FakeLLM, Json, Text

ALL = "evals/cases/eval/*.yaml"
ALPHA = "cim_project_alpha_vardhan_chemicals.pdf"


def _cases(*ids: str) -> list[Case]:
    by_id = {c.id: c for c in load_cases(ALL)}
    return [by_id[i] for i in ids]


# --- API mode -------------------------------------------------------------------------------


def test_api_mode_skips_when_the_app_lacks_endpoints() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(404, json={"code": "not_found"}))
    target = ApiTarget("https://app.test", "eval", "pw", transport=transport)
    results = cli.run_cases(load_cases(ALL)[:10], target, None, progress=False)
    assert {r.status for r in results} == {"skipped"}
    assert all("feature-unavailable" in (r.skipped_reason or "") for r in results)


class StubApp:
    """Implements just enough of the §6 contract for one QA case and one excel_op case."""

    def __init__(self, op_table: list[list[Any]]) -> None:
        self.calls: list[str] = []
        self.op_table = op_table
        self.doc_polls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append(f"{method} {path}")
        if method != "GET" and path != "/api/v1/auth/login":
            assert request.headers.get("X-CSRF-Token") == "tok", f"no CSRF header on {path}"
        if path == "/api/v1/auth/login":
            return httpx.Response(204, headers={"set-cookie": "__Host-csrf=tok; Path=/"})
        if path == "/api/v1/spaces":
            return httpx.Response(
                200, json={"items": [{"id": "s1", "kind": "private"}], "next_cursor": None}
            )
        if path == "/api/v1/spaces/s1/documents":
            return httpx.Response(202, json={"id": f"d{len(self.calls)}", "status": "pending"})
        if path.startswith("/api/v1/documents/"):
            self.doc_polls += 1
            return httpx.Response(200, json={"id": path.rsplit("/", 1)[1], "status": "ready"})
        if path == "/api/v1/conversations":
            return httpx.Response(201, json={"id": "c1"})
        if path == "/api/v1/conversations/c1/documents":
            return httpx.Response(200, json={})
        if path == "/api/v1/conversations/c1/messages":
            body = json.loads(request.content)
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=self._stream(body["content"]),
            )
        if path == "/api/v1/operation-previews/p1/applications":
            return httpx.Response(202, json={"job_id": "j1"})
        if path == "/api/v1/jobs/j1":
            return httpx.Response(200, json={"status": "succeeded", "result": {"output_id": "o1"}})
        if path == "/api/v1/conversations/c1":
            return httpx.Response(
                200, json={"messages": [{"role": "assistant", "content": "Applied the plan."}]}
            )
        if path == "/api/v1/outputs/o1/download-links":
            return httpx.Response(201, json={"token": "t1"})
        if path == "/api/v1/downloads/t1":
            wb = Workbook()
            for row in self.op_table:
                wb.active.append(row)
            buf = io.BytesIO()
            wb.save(buf)
            return httpx.Response(200, content=buf.getvalue())
        return httpx.Response(404)

    def _stream(self, text: str) -> bytes:
        doc_id = [c.split("/")[-1] for c in self.calls if c.startswith("GET /api/v1/documents/")][
            -1
        ]
        if "growth" in text:
            events = [
                ("plan", {"preview_id": "p1", "steps": ["growth"]}),
                (
                    "sources",
                    {
                        "items": [
                            {"kind": "workbook", "document_id": doc_id, "locator": "P&L!A1:K16"}
                        ]
                    },
                ),
                ("done", {"finish_reason": "stop"}),
            ]
        else:
            events = [
                ("delta", {"text": "FY2024 revenue was Rs. 1,579.4 crore."}),
                (
                    "sources",
                    {"items": [{"kind": "document", "document_id": doc_id, "locator": "p.3"}]},
                ),
                ("done", {"finish_reason": "stop", "latency_ms": 50}),
            ]
        return "".join(f"event: {n}\ndata: {json.dumps(d)}\n\n" for n, d in events).encode()


def test_api_mode_full_flow_against_a_stub_app() -> None:
    from evals.runner.scoring import load_reference  # noqa: PLC0415

    qa, op = _cases("qa-alpha-fy2024-revenue", "op-model-revenue-growth")
    reference = load_reference(op.expectations.reference_output or "")
    stub = StubApp([reference[0], *[[r[0], float(r[1])] for r in reference[1:]]])
    target = ApiTarget(
        "https://app.test", "eval", "pw", transport=httpx.MockTransport(stub), sleep=lambda _: None
    )
    results = cli.run_cases([qa, op], target, None, progress=False)
    assert [r.status for r in results] == ["passed", "passed"], [r.checks for r in results]
    assert results[1].reference_match is True
    assert stub.calls.count("POST /api/v1/auth/login") == 1  # one session per run
    assert "POST /api/v1/outputs/o1/download-links" in stub.calls


# --- model mode -----------------------------------------------------------------------------------


def test_model_mode_dry_run_over_every_case(fake_llm: FakeLLM) -> None:
    """Plumbing: every case completes against the fake; nothing errors; no raw
    planted identifier reaches the model."""
    target = ModelTarget(fake_llm.base_url, "chat")
    results = cli.run_cases(load_cases(ALL), target, None, progress=False)
    assert not [r for r in results if r.status == "error"], [r.error for r in results if r.error]
    assert {r.status for r in results} <= {"passed", "failed", "skipped", "needs_review"}
    assert any(r.skipped_reason == "mode-unsupported" for r in results)
    sent = "\n".join(json.dumps(r.body) for r in fake_llm.chat_requests)
    planted = [
        i.value for i in corpus.manifest().items if i.kind == "identifier" and i.match != "ocr"
    ]
    leaked = [v for v in planted if v in sent]
    assert leaked == []
    assert "[PAN]" in sent  # masking really happened
    assert all(
        r.body and r.body["chat_template_kwargs"] == {"enable_thinking": False}
        for r in fake_llm.chat_requests
    )


def test_model_mode_correct_answer_passes(fake_llm: FakeLLM) -> None:
    fake_llm.add(model="chat", reply=Text(f"FY2024 revenue was Rs. 1,579.4 crore [{ALPHA}#p.3]."))
    (case,) = _cases("qa-alpha-fy2024-revenue")
    (result,) = cli.run_cases([case], ModelTarget(fake_llm.base_url, "chat"), None, progress=False)
    assert result.status == "passed", result.checks
    prompt = fake_llm.last_request.messages[1]["content"]
    assert f"<<document={ALPHA} locator=p.3>>" in prompt


def test_model_mode_parses_tables_and_declines(fake_llm: FakeLLM) -> None:
    fake_llm.add(
        model="chat",
        pattern="growth",
        reply=Text(
            "| Year | Revenue growth (%) |\n|---|---|\n| FY2021 | 15.9 |\n| FY2022 | 17.9 |\n"
            "| FY2023 | 14.2 |\n| FY2024 | 12.3 | [model_vardhan_chemicals.xlsx#P&L!A1:K16]"
        ),
    )
    fake_llm.add(
        model="chat",
        pattern="buy shares",
        reply=Text("DECLINED (investment_advice): I can't advise."),
    )
    op, decline = _cases("op-model-revenue-growth", "scope-buy-recommendation")
    results = cli.run_cases(
        [op, decline], ModelTarget(fake_llm.base_url, "chat"), None, progress=False
    )
    assert results[1].status == "passed"
    from evals.runner.scoring import load_reference  # noqa: PLC0415

    expected = [r[1] for r in load_reference(op.expectations.reference_output or "")[1:]]
    if expected == ["15.9", "17.9", "14.2", "12.3"]:
        assert results[0].status == "passed", results[0].checks
    else:  # the corpus changed; the table above is then deliberately wrong
        assert results[0].reference_match is False


# --- judge and overrides --------------------------------------------------------------------------


def test_judge_scores_summary_and_override_wins(
    fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_llm.add(
        model="chat",
        pattern="Summarise the key terms of this SPA",
        reply=Text("The claims period is 24 months [spa_vardhan_chemicals.docx#para 50]."),
    )
    # Added last so it wins: the judge prompt quotes the user request too.
    fake_llm.add(
        model="chat",
        pattern="Score this",
        reply=Json({"coverage": 5, "accuracy": 4, "citations": 4, "rationale": "ok"}),
    )
    (case,) = _cases("summary-spa-key-terms")
    target, j = ModelTarget(fake_llm.base_url, "chat"), judge.Judge(fake_llm.base_url, "chat")
    (result,) = cli.run_cases([case], target, j, progress=False)
    assert result.judge and "error" not in result.judge, result.judge
    assert result.judge["scores"] == {"coverage": 5, "accuracy": 4, "citations": 4}
    assert result.summary_score == pytest.approx(13 / 3)
    assert any(c.name == "rubric" and c.passed for c in result.checks)

    overrides = tmp_path / "overrides.yaml"
    overrides.write_text(
        "overrides:\n  summary-spa-key-terms: {score: 2, reviewer: AB, note: test}\n"
    )
    monkeypatch.setattr(judge, "OVERRIDES", overrides)
    monkeypatch.setattr(cli, "load_overrides", lambda: judge.load_overrides(overrides))
    (result,) = cli.run_cases([case], target, j, progress=False)
    assert result.summary_score == 2
    assert result.status == "failed"
    assert result.override and result.override["reviewer"] == "AB"


def test_summary_without_judge_needs_review(fake_llm: FakeLLM) -> None:
    fake_llm.add(
        model="chat",
        reply=Text("The claims period is 24 months [spa_vardhan_chemicals.docx#para 50]."),
    )
    (case,) = _cases("summary-spa-key-terms")
    (result,) = cli.run_cases([case], ModelTarget(fake_llm.base_url, "chat"), None, progress=False)
    assert result.status in {"needs_review", "failed"}
    assert any(c.name == "rubric" and not c.passed for c in result.checks)


# --- reports and gates ----------------------------------------------------------------------------


def _result(case_id: str, category: str, status: str, **kw: Any) -> CaseResult:
    return CaseResult(case_id, category, status, **kw)


def test_report_and_gates(tmp_path: Path) -> None:
    results = [
        *[_result(f"qa-{i}", "qa", "passed") for i in range(8)],
        _result("qa-x", "qa", "failed", ungrounded_figures=["9,999"]),
        _result("op-1", "excel_op", "failed", reference_match=False),
        _result("sum-1", "summary", "passed", summary_score=4.3),
        _result("tbl-1", "table_extract", "skipped", skipped_reason="feature-unavailable"),
    ]
    gates = {g.name: g for g in evaluate_gates(results, load_gates())}
    assert gates["overall_pass_rate"].status == "fail"  # 9/11
    assert gates["summary_quality"].status == "pass"
    assert gates["ungrounded_figures"].status == "fail"
    assert gates["excel_reference_match"].status == "fail"
    out = write_report(
        results, list(gates.values()), {"mode": "model", "judge_model": "chat"}, tmp_path / "r"
    )
    report = (out / "report.md").read_text()
    assert "| qa | 9 | 8 | 1 | 0 |" in report
    assert "| table_extract | 1 | 0 | 0 | 1 |" in report
    assert "judge model" in report
    data = json.loads((out / "results.json").read_text())
    assert data["categories"]["qa"]["pass_rate"] == pytest.approx(8 / 9)
    assert data["overall"]["skipped"] == 1


def test_gates_without_cases_are_not_applicable() -> None:
    gates = evaluate_gates([_result("a", "qa", "skipped")], load_gates())
    assert {g.status for g in gates} == {"n/a"}


def test_cli_exit_code_follows_gates(fake_llm: FakeLLM, tmp_path: Path) -> None:
    args = [
        "run",
        "--mode",
        "model",
        "--target",
        fake_llm.base_url,
        "--no-judge",
        "--only",
        "qa-alpha-*",
        "--out",
        str(tmp_path / "a"),
    ]
    assert cli.main(args) == 1  # the fake's default text fails every case
    assert (tmp_path / "a" / "report.md").is_file()
    assert cli.main([*args[:-1], str(tmp_path / "b"), "--no-gates"]) == 0
