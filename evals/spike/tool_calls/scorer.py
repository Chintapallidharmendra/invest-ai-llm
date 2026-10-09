"""Score one model reply against an expected tool call.

Three levels, each implying the previous (AC #2):

- **valid**: the reply's call (or deliberate non-call) is well formed: a known tool,
  JSON-object arguments that satisfy the tool's schema, and no raw ``<tool_call>`` text
  left in the content (a hermes parser failure).
- **right_tool**: the expected tool was called (or, if none was expected, none was).
- **right_args**: every expected argument matcher holds.

Matchers (per argument, in ``prompts.yaml``): ``equals``, ``one_of``, ``contains_any``
(case-insensitive substring), ``range: [lo, hi]``, ``length``, ``present``,
``contains`` (a list includes all of these values) and ``items`` (matchers per list
index, for ``calculate`` inputs).
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from evals.spike.tool_calls.tools import BY_NAME

_RAW_TOOL_CALL: Final = re.compile(r"<tool_call>|\"name\"\s*:\s*\"[a-z_]+\"\s*,\s*\"arguments\"")


class Failure(StrEnum):
    MALFORMED_JSON = "malformed_json"
    UNKNOWN_TOOL = "unknown_tool"
    SCHEMA = "schema"
    UNPARSED_TOOL_CALL = "unparsed_tool_call"
    NO_CALL = "no_call"
    UNEXPECTED_CALL = "unexpected_call"
    WRONG_TOOL = "wrong_tool"
    WRONG_ARGS = "wrong_args"


@dataclass(frozen=True)
class Expectation:
    tool: str | None  # None: the model should answer without a tool
    args: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class Score:
    valid: bool
    right_tool: bool
    right_args: bool
    failure: Failure | None = None
    detail: str = ""


# --- Schema validation (the subset the tool schemas use) -----------------------------


def schema_errors(schema: Mapping[str, Any], value: Any, path: str = "$") -> list[str]:  # noqa: PLR0911, PLR0912
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return [f"{path}: not an object"]
        errors = [f"{path}.{k}: missing" for k in schema.get("required", []) if k not in value]
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            errors += [f"{path}.{k}: not allowed" for k in value if k not in properties]
        for key, sub in properties.items():
            if key in value:
                errors += schema_errors(sub, value[key], f"{path}.{key}")
        return errors
    if kind == "array":
        if not isinstance(value, list):
            return [f"{path}: not an array"]
        errors = []
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: too many items")
        for i, item in enumerate(value):
            errors += schema_errors(schema.get("items", {}), item, f"{path}[{i}]")
        return errors
    if kind == "string":
        if not isinstance(value, str):
            return [f"{path}: not a string"]
        if "enum" in schema and value not in schema["enum"]:
            return [f"{path}: not one of {schema['enum']}"]
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 10**9):
            return [f"{path}: bad length"]
        return []
    if kind in {"integer", "number"}:
        numeric = isinstance(value, int | float) and not isinstance(value, bool)
        if not numeric or (kind == "integer" and not isinstance(value, int)):
            return [f"{path}: not an {kind}"]
        if "minimum" in schema and value < schema["minimum"]:
            return [f"{path}: below minimum"]
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            return [f"{path}: not above minimum"]
        return []
    return []


# --- Matchers ------------------------------------------------------------------------------


def matches(matcher: Mapping[str, Any], value: Any) -> bool:
    for kind, expected in matcher.items():
        if kind == "equals":
            ok = _norm(value) == _norm(expected)
        elif kind == "one_of":
            ok = _norm(value) in {_norm(e) for e in expected}
        elif kind == "contains_any":
            ok = isinstance(value, str) and any(e.casefold() in value.casefold() for e in expected)
        elif kind == "range":
            lo, hi = expected
            ok = isinstance(value, int | float) and lo <= value <= hi
        elif kind == "length":
            ok = isinstance(value, list) and len(value) == expected
        elif kind == "present":
            ok = (value is not None) == bool(expected)
        elif kind == "contains":
            ok = isinstance(value, list) and all(
                _norm(e) in {_norm(v) for v in value} for e in expected
            )
        elif kind == "items":
            ok = (
                isinstance(value, list)
                and len(value) >= len(expected)
                and all(
                    all(matches(m, (item or {}).get(k)) for k, m in spec.items())
                    for spec, item in zip(expected, value, strict=False)
                )
            )
        else:
            raise ValueError(f"unknown matcher: {kind}")
        if not ok:
            return False
    return True


def _norm(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip().casefold().replace(",", "")
        try:
            return float(text)
        except ValueError:
            return text
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return value


# --- Scoring ------------------------------------------------------------------------------


def first_call(message: Mapping[str, Any]) -> Mapping[str, Any] | None:
    calls = message.get("tool_calls") or []
    return calls[0] if calls else None


def score(message: Mapping[str, Any], expected: Expectation) -> Score:  # noqa: PLR0911
    """Score an assistant ``message`` (OpenAI shape) against ``expected``."""
    content = message.get("content") or ""
    call = first_call(message)
    if call is None:
        if _RAW_TOOL_CALL.search(content):
            return Score(
                False, False, False, Failure.UNPARSED_TOOL_CALL, "tool call left in content"
            )
        if expected.tool is None:
            return Score(True, True, True)
        return Score(True, False, False, Failure.NO_CALL)

    function = call.get("function") or {}
    name = function.get("name")
    raw = function.get("arguments") or "{}"
    try:
        arguments = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return Score(False, False, False, Failure.MALFORMED_JSON)
    if name not in BY_NAME:
        return Score(False, False, False, Failure.UNKNOWN_TOOL, str(name))
    errors = schema_errors(BY_NAME[name]["function"]["parameters"], arguments)
    if errors:
        valid, failure, detail = False, Failure.SCHEMA, "; ".join(errors[:3])
    else:
        valid, failure, detail = True, None, ""

    if expected.tool is None:
        return Score(valid, False, False, failure or Failure.UNEXPECTED_CALL, str(name))
    if name != expected.tool:
        return Score(valid, False, False, failure or Failure.WRONG_TOOL, str(name))
    wrong = [k for k, m in expected.args.items() if not matches(m, arguments.get(k))]
    if not valid:
        return Score(False, True, False, failure, detail)
    if wrong:
        return Score(True, True, False, Failure.WRONG_ARGS, ", ".join(sorted(wrong)))
    return Score(True, True, True)


@dataclass(frozen=True)
class Summary:
    total: int
    valid: int
    right_tool: int
    right_args: int
    failures: Mapping[str, int]

    def rate(self, field: str) -> float:
        return getattr(self, field) / self.total if self.total else 0.0


def summarise(scores: Sequence[Score]) -> Summary:
    failures: dict[str, int] = {}
    for s in scores:
        if s.failure is not None:
            failures[s.failure.value] = failures.get(s.failure.value, 0) + 1
    return Summary(
        total=len(scores),
        valid=sum(s.valid for s in scores),
        right_tool=sum(s.right_tool for s in scores),
        right_args=sum(s.right_args for s in scores),
        failures=failures,
    )
