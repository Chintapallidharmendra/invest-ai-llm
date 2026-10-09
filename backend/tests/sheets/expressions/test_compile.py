"""Type checks, result types, used columns and error codes (AC #2, #3)."""

import pytest

from app.sheets.expressions import (
    MAX_DEPTH,
    MAX_LENGTH,
    MAX_NODES,
    ColumnType,
    ErrorCode,
    ExpressionError,
    compile_expression,
)
from tests.sheets.expressions.conftest import SCHEMA


@pytest.mark.parametrize(
    ("text", "dtype"),
    [
        ("Units + 1", ColumnType.INTEGER),
        ("Units * Units", ColumnType.INTEGER),
        ("Units + 1.5", ColumnType.NUMBER),
        ("Units / 1", ColumnType.NUMBER),
        ("Revenue - Cost", ColumnType.NUMBER),
        ("Revenue > Cost", ColumnType.BOOLEAN),
        ('IF(Active, "a", "b")', ColumnType.TEXT),
        ("IF(Active, Units, 1)", ColumnType.INTEGER),
        ("IF(Active, Units, 1.0)", ColumnType.NUMBER),
        ("IF(Active, Day, Day2)", ColumnType.DATE),
        ("ROUND(Units, -1)", ColumnType.INTEGER),
        ("ROUND(Revenue)", ColumnType.NUMBER),
        ("ABS(Units)", ColumnType.INTEGER),
        ("MIN(Units, [*])", ColumnType.INTEGER),
        ("MAX(Units, Revenue)", ColumnType.NUMBER),
        ("NOT Active", ColumnType.BOOLEAN),
    ],
)
def test_result_type(text: str, dtype: ColumnType) -> None:
    assert compile_expression(text, SCHEMA).dtype is dtype


def test_columns_used() -> None:
    compiled = compile_expression("IF([Active], [Revenue] / Units, [Cost])", SCHEMA)
    assert compiled.columns_used == {"Active", "Revenue", "Units", "Cost"}
    assert compile_expression("1 + 2", SCHEMA).columns_used == frozenset()


def test_column_lookup_forgives_case_and_spaces() -> None:
    assert compile_expression("revenue", SCHEMA).columns_used == {"Revenue"}
    assert compile_expression("[ margin % ]", SCHEMA).columns_used == {"Margin %"}


def test_ambiguous_case_insensitive_lookup_is_unknown() -> None:
    schema = {"Sales": ColumnType.NUMBER, "SALES": ColumnType.NUMBER}
    assert compile_expression("[Sales]", schema).columns_used == {"Sales"}
    with pytest.raises(ExpressionError) as caught:
        compile_expression("sales", schema)
    assert caught.value.code is ErrorCode.UNKNOWN_COLUMN


@pytest.mark.parametrize(
    ("text", "code", "position", "names"),
    [
        ("Revenue +", ErrorCode.SYNTAX, 9, ()),
        ("Revenue ? 2", ErrorCode.SYNTAX, 8, ()),
        ("[Revenue] + [Profit]", ErrorCode.UNKNOWN_COLUMN, 12, ("Profit",)),
        ("Revenue + Profit", ErrorCode.UNKNOWN_COLUMN, 10, ("Profit",)),
        ("SQRT(Revenue)", ErrorCode.UNKNOWN_FUNCTION, 0, ("SQRT",)),
        ("1 + eval(Revenue)", ErrorCode.UNKNOWN_FUNCTION, 4, ("eval",)),
        ("[Revenue] / [Region]", ErrorCode.TYPE_MISMATCH, 10, ()),
        ("Region > 1", ErrorCode.TYPE_MISMATCH, 7, ()),
        ("Active + 1", ErrorCode.TYPE_MISMATCH, 7, ()),
        ("-Region", ErrorCode.TYPE_MISMATCH, 0, ()),
        ("NOT Revenue", ErrorCode.TYPE_MISMATCH, 0, ()),
        ("Revenue AND Active", ErrorCode.TYPE_MISMATCH, 8, ()),
        ("IF(Revenue, 1, 2)", ErrorCode.TYPE_MISMATCH, 0, ("IF",)),
        ('IF(Active, 1, "x")', ErrorCode.TYPE_MISMATCH, 0, ("IF",)),
        ("ROUND(Region)", ErrorCode.TYPE_MISMATCH, 0, ("ROUND",)),
        ("ROUND(Revenue, Units)", ErrorCode.TYPE_MISMATCH, 0, ("ROUND",)),
        ("ROUND(Revenue, 1.5)", ErrorCode.TYPE_MISMATCH, 0, ("ROUND",)),
        ("ROUND(Revenue, 16)", ErrorCode.TYPE_MISMATCH, 0, ("ROUND",)),
        ("MAX(Revenue, Region)", ErrorCode.TYPE_MISMATCH, 0, ("MAX",)),
        ("Day + 1", ErrorCode.TYPE_MISMATCH, 4, ()),
        ("Day < Revenue", ErrorCode.TYPE_MISMATCH, 4, ()),
        ("IF(Active, 1)", ErrorCode.SYNTAX, 0, ("IF",)),
        ("ABS(1, 2)", ErrorCode.SYNTAX, 0, ("ABS",)),
        ("MIN()", ErrorCode.SYNTAX, 0, ("MIN",)),
        ("1e999", ErrorCode.SYNTAX, 0, ()),
        ("   ", ErrorCode.SYNTAX, 0, ()),
    ],
)
def test_error_codes_positions_and_messages(
    text: str, code: ErrorCode, position: int, names: tuple[str, ...]
) -> None:
    with pytest.raises(ExpressionError) as caught:
        compile_expression(text, SCHEMA)
    error = caught.value
    assert (error.code, error.position) == (code, position)
    assert str(error) == error.message
    for name in names:
        assert name in error.message


def test_message_names_only_the_column() -> None:
    with pytest.raises(ExpressionError) as caught:
        compile_expression('[Secret Deal] = "Project Falcon"', SCHEMA)
    assert "Secret Deal" in caught.value.message
    assert "Falcon" not in caught.value.message


def test_too_long() -> None:
    text = "1" + " + 1" * (MAX_LENGTH // 4)
    assert len(text) > MAX_LENGTH
    with pytest.raises(ExpressionError) as caught:
        compile_expression(text, SCHEMA)
    assert caught.value.code is ErrorCode.TOO_LONG
    compile_expression("1" + " " * (MAX_LENGTH - 1), SCHEMA)  # exactly at the limit is fine


def test_too_deep() -> None:
    ok = "(" * MAX_DEPTH + "1" + ")" * MAX_DEPTH
    compile_expression(ok, SCHEMA)
    deep = "(" * (MAX_DEPTH + 1) + "1" + ")" * (MAX_DEPTH + 1)
    with pytest.raises(ExpressionError) as caught:
        compile_expression(deep, SCHEMA)
    assert caught.value.code is ErrorCode.TOO_DEEP
    assert caught.value.position == MAX_DEPTH + 1  # the first construct past the limit


def test_deep_unary_and_parens_up_to_the_length_limit() -> None:
    for text in ("(" * 450 + "1" + ")" * 450, "-" * 999 + "1", "NOT " * 240 + "TRUE"):
        with pytest.raises(ExpressionError) as caught:
            compile_expression(text, SCHEMA)
        assert caught.value.code is ErrorCode.TOO_DEEP


def test_too_complex() -> None:
    text = "+".join(["1"] * (MAX_NODES // 2 + 10))
    assert len(text) <= MAX_LENGTH
    with pytest.raises(ExpressionError) as caught:
        compile_expression(text, SCHEMA)
    assert caught.value.code is ErrorCode.TOO_COMPLEX


def test_flat_but_large_is_fine() -> None:
    compile_expression("MAX(" + ", ".join(["Revenue"] * 100) + ")", SCHEMA)
