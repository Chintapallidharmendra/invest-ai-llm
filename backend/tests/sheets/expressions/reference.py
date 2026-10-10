"""A pure-Python reference evaluator for the typed AST (test-only).

It walks :func:`parse_expression`'s nodes row by row, written independently of the
Polars compiler, so the compiled expressions can be checked against it.
"""

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.sheets.expressions import (
    Binary,
    Call,
    Column,
    ColumnType,
    Function,
    Literal,
    Node,
    Unary,
    round_digits,
)

Row = Mapping[str, Any]


def evaluate(node: Node, row: Row) -> Any:  # noqa: PLR0911 (one branch per node kind)
    match node:
        case Literal(value=value):
            return value
        case Column(name=name):
            return row[name]
        case Unary(op="-", operand=operand):
            value = evaluate(operand, row)
            return None if value is None else -value
        case Unary(operand=operand):
            value = evaluate(operand, row)
            return None if value is None else not value
        case Binary(op="AND", left=left, right=right):
            a, b = evaluate(left, row), evaluate(right, row)
            if a is False or b is False:
                return False
            return None if a is None or b is None else True
        case Binary(op="OR", left=left, right=right):
            a, b = evaluate(left, row), evaluate(right, row)
            if a is True or b is True:
                return True
            return None if a is None or b is None else False
        case Binary(op=op, left=left, right=right):
            a, b = evaluate(left, row), evaluate(right, row)
            if a is None or b is None:
                return None
            return _binary(op, a, b, node.dtype)
        case Call():
            return _call(node, row)
    raise AssertionError(node)


def _binary(op: str, a: Any, b: Any, dtype: ColumnType) -> Any:  # noqa: PLR0911
    match op:
        case "+":
            return _num(a + b, dtype)
        case "-":
            return _num(a - b, dtype)
        case "*":
            return _num(a * b, dtype)
        case "/":
            return None if b == 0 else float(a) / float(b)
        case "=":
            return a == b
        case "<>":
            return a != b
        case "<":
            return a < b
        case "<=":
            return a <= b
        case ">":
            return a > b
        case ">=":
            return a >= b
    raise AssertionError(op)


def _num(value: Any, dtype: ColumnType) -> Any:
    return int(value) if dtype is ColumnType.INTEGER else float(value)


def _call(node: Call, row: Row) -> Any:  # noqa: PLR0911
    match node.function:
        case Function.IF:
            condition = evaluate(node.args[0], row)
            if condition is None:
                return None
            chosen = evaluate(node.args[1] if condition else node.args[2], row)
            if chosen is None or not _numeric(node):
                return chosen
            return _num(chosen, node.dtype)
        case Function.ROUND:
            value = evaluate(node.args[0], row)
            if value is None:
                return None
            exponent = Decimal(1).scaleb(-round_digits(node))
            rounded = Decimal(repr(value)).quantize(exponent, rounding=ROUND_HALF_UP)
            return _num(rounded, node.dtype)
        case Function.ABS:
            value = evaluate(node.args[0], row)
            return None if value is None else abs(value)
        case Function.MIN | Function.MAX:
            values = [v for v in (evaluate(a, row) for a in node.args) if v is not None]
            if not values:
                return None
            pick = min if node.function is Function.MIN else max
            return _num(pick(values), node.dtype)
    raise AssertionError(node)


def _numeric(node: Node) -> bool:
    return node.dtype in {ColumnType.NUMBER, ColumnType.INTEGER}


def evaluate_rows(node: Node, rows: Sequence[Row]) -> list[Any]:
    return [evaluate(node, row) for row in rows]
