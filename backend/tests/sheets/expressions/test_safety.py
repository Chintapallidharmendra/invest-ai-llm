"""No code execution, and nothing but ExpressionError under fuzzing (AC #5)."""

import ast
import random
import time
from pathlib import Path
from typing import Final

import polars as pl

import app.sheets.expressions as expressions_module
from app.sheets.expressions import ExpressionError, compile_expression
from tests.sheets.expressions.conftest import SCHEMA, frame
from tests.sheets.expressions.test_grammar import REJECTED
from tests.sheets.expressions.test_semantics import CASES

FUZZ_INPUTS: Final = 10_000
PER_INPUT_BUDGET_S: Final = 0.050
SEED: Final = 20261009

_VOCAB: Final = [
    *("+", "-", "*", "/", "=", "<>", "<", "<=", ">", ">=", "(", ")", ",", " ", " ", " "),
    *("AND", "OR", "NOT", "TRUE", "FALSE", "and", "Not", "IF", "ROUND", "ABS", "MIN", "MAX"),
    *("if(", "round(", "0", "1", "2.5", ".5", "1e3", "1e999", "99999999999999999999"),
    *('"text"', '""', '"a""b"', "[Revenue]", "[Units]", "[Region]", "[Active]", "[Day]"),
    *("Revenue", "Units", "Active", "[Margin %]", "[a]]b]", "[*]", "[Ünïcode name]", "[nope]"),
    # Things that must never do anything but fail to parse or type-check.
    *("__class__", "__import__", "eval", "exec", "compile", "os", "sys", ".", "..", ":"),
    *("lambda", "import", ";", "`", "[", "]", "{", "}", "'", "\\", "\n", "\x00", "#", "@"),
    *("**", "//", "%", "!=", "==", "&", "|", "~", "^", "$"),
    # Right-to-left override, byte-order mark, a double-struck R.
    *(chr(0x202E), chr(0xFEFF), chr(0x211D)),
]
_ALPHABET: Final = "".join(sorted({c for token in _VOCAB for c in token} | set("abcXYZ09_")))


def _fuzz_inputs(rng: random.Random) -> list[str]:
    seeds = [*CASES, *REJECTED]
    inputs = []
    for i in range(FUZZ_INPUTS):
        kind = i % 4
        if kind == 0:  # random token sequences
            inputs.append("".join(rng.choice(_VOCAB) for _ in range(rng.randint(1, 40))))
        elif kind == 1:  # random characters, occasionally past the length limit
            length = rng.choice([rng.randint(0, 60), rng.randint(900, 1100)])
            inputs.append("".join(rng.choice(_ALPHABET) for _ in range(length)))
        elif kind == 2:  # mutated valid expressions
            text = list(rng.choice(seeds))
            for _ in range(rng.randint(1, 4)):
                position = rng.randrange(len(text) + 1)
                action = rng.random()
                if action < 0.4 and text:
                    del text[min(position, len(text) - 1)]
                elif action < 0.8:
                    text.insert(position, rng.choice(_VOCAB))
                else:
                    text = text[:position] + text[:position]
            inputs.append("".join(text))
        else:  # deep or long structures
            depth = rng.randint(1, 120)
            opener = rng.choice(["(", "-", "NOT ", "ABS(", "IF(TRUE, ", "MIN(1, "])
            closer = {"(": ")", "ABS(": ")", "IF(TRUE, ": ", 0)", "MIN(1, ": ")"}.get(opener, "")
            inputs.append(opener * depth + rng.choice(["1", "Revenue", "TRUE"]) + closer * depth)
    return inputs


def test_fuzz_only_expression_errors_and_fast() -> None:
    rng = random.Random(SEED)  # noqa: S311 (reproducible fuzzing, not crypto)
    data = frame()
    compiled_count = 0
    slowest = 0.0
    bad_positions = []
    for text in _fuzz_inputs(rng):
        started = time.perf_counter()
        try:
            compiled = compile_expression(text, SCHEMA)
        except ExpressionError as error:
            if not 0 <= error.position <= max(len(text), 1000):
                bad_positions.append((text, error.position))
        else:
            compiled_count += 1
            # Valid expressions also evaluate without raising.
            data.with_columns(compiled.expr.alias("out"))
        elapsed = time.perf_counter() - started
        slowest = max(slowest, elapsed)
        assert elapsed <= PER_INPUT_BUDGET_S, (elapsed, len(text))
    assert not bad_positions
    assert compiled_count > 100  # the fuzzer reaches valid expressions too
    assert slowest <= PER_INPUT_BUDGET_S


def test_no_dynamic_execution_in_the_module() -> None:
    source = Path(expressions_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called & {"eval", "exec", "compile", "__import__", "setattr", "vars"}
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert not imported & {"importlib", "builtins", "operator", "pickle"}


def test_column_names_that_look_like_patterns_are_literal() -> None:
    # pl.col("*") or pl.col("^...$") would select many columns; names must be exact.
    compiled = compile_expression("[*] + 1", SCHEMA)
    out = frame().with_columns(compiled.expr.alias("out"))["out"]
    assert out.dtype == pl.Int64
    assert out.to_list()[:2] == [4, 1]
    schema = {"^.*$": SCHEMA["Units"], "Units": SCHEMA["Units"]}
    data = pl.DataFrame({"^.*$": [1, 2], "Units": [10, 20]})
    result = data.with_columns(compile_expression("[^.*$] * 2", schema).expr.alias("out"))
    assert result["out"].to_list() == [2, 4]
