"""AC #2: the tool-call prompts and the scorer (wrong tool, wrong arguments, malformed
JSON, no call, unparsed calls), and a run against the fake LLM."""

import json
from typing import Any

import pytest

from evals.spike.client import Endpoint
from evals.spike.tool_calls import runner
from evals.spike.tool_calls.scorer import Expectation, Failure, matches, schema_errors, score
from evals.spike.tool_calls.tools import BY_NAME, TOOLS
from tests.fakes.fake_llm import FakeLLM, Text, ToolCall, ToolCalls

DOC = "0198f3a2-0000-7000-8000-000000000001"


def _call(name: str, arguments: Any) -> dict[str, Any]:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {"content": None, "tool_calls": [{"id": "c1", "type": "function",
            "function": {"name": name, "arguments": raw}}]}  # fmt: skip


READ = Expectation(
    "read_pages",
    {"document_id": {"equals": DOC}, "from": {"equals": 12}, "to": {"equals": 14}},
)


def test_prompts_cover_every_tool_and_enough_cases() -> None:
    system, cases = runner.load_cases()
    assert len(cases) >= runner.MIN_PROMPTS
    assert len({c.id for c in cases}) == len(cases)
    assert {c.expected.tool for c in cases} - {None} == set(BY_NAME)
    assert any(c.expected.tool is None for c in cases)
    assert DOC in system
    # Every expected argument names a real parameter of its tool.
    for case in cases:
        if case.expected.tool:
            params = BY_NAME[case.expected.tool]["function"]["parameters"]["properties"]
            assert set(case.expected.args) <= set(params), case.id


def test_tool_schemas_are_bounded() -> None:
    assert len(TOOLS) == 11
    for tool in TOOLS:
        parameters = tool["function"]["parameters"]
        assert parameters["additionalProperties"] is False


def test_right_call() -> None:
    result = score(_call("read_pages", {"document_id": DOC, "from": 12, "to": 14}), READ)
    assert (result.valid, result.right_tool, result.right_args) == (True, True, True)


def test_wrong_tool() -> None:
    result = score(_call("search_documents", {"query": "page 12"}), READ)
    assert (result.valid, result.right_tool, result.failure) == (True, False, Failure.WRONG_TOOL)


def test_wrong_arguments() -> None:
    result = score(_call("read_pages", {"document_id": DOC, "from": 11, "to": 14}), READ)
    assert (result.valid, result.right_tool, result.right_args) == (True, True, False)
    assert result.failure is Failure.WRONG_ARGS
    assert result.detail == "from"


def test_malformed_json() -> None:
    result = score(_call("read_pages", '{"document_id": "x", "from": 12,'), READ)
    assert (result.valid, result.failure) == (False, Failure.MALFORMED_JSON)


def test_schema_violation_is_not_valid() -> None:
    result = score(_call("read_pages", {"document_id": DOC, "from": "12", "to": 14}), READ)
    assert (result.valid, result.right_tool, result.right_args) == (False, True, False)
    assert result.failure is Failure.SCHEMA
    extra = score(_call("read_pages", {"document_id": DOC, "from": 12, "to": 14, "x": 1}), READ)
    assert extra.failure is Failure.SCHEMA


def test_unknown_tool() -> None:
    assert score(_call("run_python", {"code": "1+1"}), READ).failure is Failure.UNKNOWN_TOOL


def test_no_call() -> None:
    result = score({"content": "Page 12 says revenue grew."}, READ)
    assert (result.valid, result.right_tool, result.failure) == (True, False, Failure.NO_CALL)


def test_unparsed_hermes_call_in_content() -> None:
    message = {"content": '<tool_call>{"name": "read_pages", "arguments": {}}</tool_call>'}
    result = score(message, READ)
    assert (result.valid, result.failure) == (False, Failure.UNPARSED_TOOL_CALL)


def test_no_tool_expected() -> None:
    none = Expectation(None, {})
    assert score({"content": "EBITDA is earnings before ..."}, none).right_args
    unexpected = score(_call("list_selected_documents", {}), none)
    assert (unexpected.right_tool, unexpected.failure) == (False, Failure.UNEXPECTED_CALL)


def test_calculate_inputs_matchers() -> None:
    expected = Expectation(
        "calculate",
        {"metric": {"equals": "cagr"},
         "inputs": {"length": 2, "items": [{"value": {"equals": "812.4"}}, {"value": {"equals": "1250.0"}}]}},
    )  # fmt: skip
    good = {"metric": "cagr", "inputs": [{"value": "812.4", "unit": "INR"},
            {"value": "1,250", "unit": "INR"}], "years": 3}  # fmt: skip
    assert score(_call("calculate", good), expected).right_args
    swapped = {**good, "inputs": list(reversed(good["inputs"]))}
    assert not score(_call("calculate", swapped), expected).right_args


@pytest.mark.parametrize(
    ("matcher", "value", "ok"),
    [
        ({"equals": "Invoices"}, "invoices", True),
        ({"equals": 12}, "12", True),
        ({"one_of": ["a", "b"]}, "B", True),
        ({"contains_any": ["EBITDA"]}, "fy25 ebitda margin", True),
        ({"contains_any": ["debt"]}, "revenue", False),
        ({"range": [40, 41]}, 41, True),
        ({"range": [40, 41]}, 42, False),
        ({"length": 2}, [1, 2], True),
        ({"present": True}, None, False),
        ({"contains": ["x"]}, ["y", "x"], True),
        ({"contains": ["x"]}, None, False),
    ],
)
def test_matchers(matcher: dict[str, Any], value: Any, ok: bool) -> None:
    assert matches(matcher, value) is ok


def test_schema_errors_cover_types() -> None:
    schema = BY_NAME["calculate"]["function"]["parameters"]
    assert (
        schema_errors(schema, {"metric": "cagr", "inputs": [{"value": "1", "unit": "INR"}]}) == []
    )
    errors = schema_errors(schema, {"metric": "median", "inputs": [], "years": 0})
    assert any("metric" in e for e in errors)
    assert any("too few" in e for e in errors)
    assert any("years" in e for e in errors)


async def test_run_against_the_fake_llm(fake_llm: FakeLLM) -> None:
    system, cases = runner.load_cases()
    fake_llm.add(pattern=r"Read pages 12 to 14", reply=ToolCalls(
        [ToolCall("read_pages", {"document_id": DOC, "from": 12, "to": 14})]))  # fmt: skip
    fake_llm.add(pattern=r"what does EBITDA measure", reply=Text("It measures operating profit."))
    picked = [c for c in cases if c.id in {"tc09", "tc33", "tc03"}]
    result = await runner.run(Endpoint(fake_llm.base_url), cases=picked, system=system)
    by_id = {c["id"]: c for c in result["cases"]}
    assert by_id["tc09"]["right_args"] is True
    assert by_id["tc33"]["right_args"] is True
    assert by_id["tc03"]["failure"] == "no_call"  # the fake's default text reply
    assert result["summary"]["total"] == 3
    request = fake_llm.chat_requests[0]
    assert request.body["tools"] == TOOLS
    assert request.body["chat_template_kwargs"] == {"enable_thinking": False}
