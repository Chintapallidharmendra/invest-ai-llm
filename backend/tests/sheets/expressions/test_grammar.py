"""What parses, and what never does (AC #1, #5)."""

import pytest

from app.sheets.expressions import (
    Binary,
    Call,
    Column,
    ColumnType,
    ErrorCode,
    ExpressionError,
    Function,
    Literal,
    parse_expression,
)
from tests.sheets.expressions.conftest import SCHEMA


@pytest.mark.parametrize(
    "text",
    [
        "12",
        "1.5",
        ".5",
        "2e6",
        '"text"',
        '"say ""hi"""',
        "TRUE",
        "false",
        "[Revenue]",
        "Revenue",
        "[Margin %]",
        "[Ünïcode name]",
        "[a]]b]",
        "[*]",
        "1 + 2 - 3 * 4 / 5",
        "-Revenue",
        "Revenue = Cost",
        "Revenue <> Cost",
        "Revenue < Cost",
        "Revenue <= Cost",
        "Revenue > Cost",
        "Revenue >= Cost",
        "Active AND Active OR NOT Active",
        "(1 + 2) * 3",
        "IF(Active, 1, 2)",
        "ROUND(Revenue)",
        "ROUND(Revenue, 2)",
        "ABS(Revenue)",
        "MIN(Revenue, Cost, 1)",
        "MAX(Revenue, Cost)",
        "  Revenue\n+\tCost  ",
    ],
)
def test_constructs_parse(text: str) -> None:
    parse_expression(text, SCHEMA)


@pytest.mark.parametrize("text", ["if(Active,1,2)", "If(Active,1,2)", "Round(1)", "abs(1)"])
def test_functions_are_case_insensitive(text: str) -> None:
    node = parse_expression(text, SCHEMA)
    assert isinstance(node, Call)


@pytest.mark.parametrize("text", ["Active and Active", "Active AnD Active", "true OR False"])
def test_keywords_are_case_insensitive(text: str) -> None:
    assert isinstance(parse_expression(text, SCHEMA), Binary)


def test_keyword_prefix_is_still_a_name() -> None:
    schema = {"order": ColumnType.NUMBER, "notes": ColumnType.TEXT, "android": ColumnType.NUMBER}
    assert parse_expression("order + android", schema) == Binary(
        "+",
        Column("order", ColumnType.NUMBER, 0),
        Column("android", ColumnType.NUMBER, 8),
        ColumnType.NUMBER,
        6,
    )
    assert isinstance(parse_expression("notes", schema), Column)


def test_precedence() -> None:
    node = parse_expression("1 + 2 * 3", SCHEMA)
    assert isinstance(node, Binary)
    assert node.op == "+"
    assert isinstance(node.right, Binary)
    assert node.right.op == "*"


def test_literal_values() -> None:
    assert parse_expression('"a ""b"""', SCHEMA) == Literal('a "b"', ColumnType.TEXT, 0)
    assert parse_expression("-5", SCHEMA) == Literal(-5, ColumnType.INTEGER, 0)
    # Too big for Int64: becomes a float.
    big = parse_expression("123456789012345678901234567890", SCHEMA)
    assert isinstance(big, Literal)
    assert big.dtype is ColumnType.NUMBER


def test_round_call_shape() -> None:
    node = parse_expression("ROUND(Revenue, -2)", SCHEMA)
    assert isinstance(node, Call)
    assert node.function is Function.ROUND
    assert node.args[1] == Literal(-2, ColumnType.INTEGER, 15)


REJECTED = [
    "Revenue.real",  # attribute access
    "a.b.c",  # attribute chain
    "__class__",  # dunder
    "Revenue.__class__",
    "_private",
    "lambda: 1",
    "lambda x: x",
    "import os",
    "__import__('os')",
    "Revenue; Cost",
    "`whoami`",
    "[a][b]",  # subscript
    "Revenue[0]",
    "Revenue ** 2",
    "Revenue // 2",
    "Revenue % 2",
    "Revenue == Cost",
    "Revenue != Cost",
    "1 < 2 < 3",
    "'single quoted'",
    "Revenue & Cost",
    "x if Active else y",
    "{1: 2}",
    "(1, 2)",
    "f(x)(y)",
    "ROUND(Revenue",
    "Revenue +",
    "()",
    "1 2",
    "@Revenue",
    "$Revenue",
    "#Revenue",
]


def test_enough_rejected_constructs() -> None:
    assert len(REJECTED) >= 20


@pytest.mark.parametrize("text", REJECTED)
def test_rejected_constructs(text: str) -> None:
    with pytest.raises(ExpressionError) as caught:
        parse_expression(text, SCHEMA)
    assert caught.value.code is ErrorCode.SYNTAX
