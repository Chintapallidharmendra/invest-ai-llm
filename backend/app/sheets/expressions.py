"""Calculated-column expressions compiled to Polars, never evaluated (Story 10.3; ADR-030).

The language is the closed grammar in ``expressions.lark``: literals, column references
(``[Column Name]`` or a bare identifier), ``+ - * /``, unary ``-``, comparisons
``= <> < <= > >=``, ``AND OR NOT``, parentheses and the functions ``IF``, ``ROUND``,
``ABS``, ``MIN`` and ``MAX``. Nothing else parses, and nothing is ever passed to
``eval``: the parse tree is checked and typed, then translated node by node into a
``pl.Expr``::

    compiled = compile_expression("ROUND([EBITDA] / [Revenue] * 100, 1)", schema)
    frame.with_columns(compiled.expr.alias("EBITDA margin %"))

Semantics (chosen to match Excel where Polars differs):

- Division always yields a float, and dividing by zero yields null, never ``inf``.
- Null propagates through arithmetic, comparisons and ``ROUND``/``ABS``. An ``IF``
  whose condition is null is null. ``AND``/``OR`` use three-valued logic
  (``null AND false`` is false). ``MIN``/``MAX`` skip nulls, as Excel skips blanks.
- ``ROUND(x, n)`` rounds half away from zero; ``n`` is a whole-number literal from -15
  to 15 (negative rounds to tens, hundreds, ...).
- Integer arithmetic stays integer (Int64) except division. Overflow beyond Int64 is
  outside the range of sheet data and is not checked.

Limits: 1,000 characters, 32 levels of nesting (parentheses, functions, ``NOT`` and
unary minus count; a flat chain like ``a + b - c`` is one level) and 500 parts
(operators, names and literals).
Every failure is an :class:`ExpressionError` whose message names at most the column
or function involved.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Final

import polars as pl
import polars.selectors as cs
from lark import Lark, Token, Tree
from lark.exceptions import LarkError, UnexpectedCharacters, UnexpectedEOF, UnexpectedToken

MAX_LENGTH: Final = 1_000
MAX_DEPTH: Final = 32
MAX_NODES: Final = 500
MAX_ROUND_DIGITS: Final = 15

_INT64_MIN: Final = -(2**63)
_INT64_MAX: Final = 2**63 - 1
_INTEGER_LITERAL: Final = re.compile(r"[0-9]+")


class ColumnType(StrEnum):
    NUMBER = "number"
    INTEGER = "integer"
    TEXT = "text"
    BOOLEAN = "boolean"
    DATE = "date"


_NUMERIC: Final = frozenset({ColumnType.NUMBER, ColumnType.INTEGER})


class ErrorCode(StrEnum):
    SYNTAX = "syntax"
    UNKNOWN_COLUMN = "unknown_column"
    UNKNOWN_FUNCTION = "unknown_function"
    TYPE_MISMATCH = "type_mismatch"
    TOO_LONG = "too_long"
    TOO_DEEP = "too_deep"
    TOO_COMPLEX = "too_complex"


class ExpressionError(ValueError):
    """An expression that can't be compiled. ``position`` is a 0-based character offset."""

    def __init__(self, code: ErrorCode, message: str, position: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.position = position


class Function(StrEnum):
    IF = "IF"
    ROUND = "ROUND"
    ABS = "ABS"
    MIN = "MIN"
    MAX = "MAX"


# Inclusive (min, max) argument counts; None means no upper bound.
_ARITY: Final[Mapping[Function, tuple[int, int | None]]] = {
    Function.IF: (3, 3),
    Function.ROUND: (1, 2),
    Function.ABS: (1, 1),
    Function.MIN: (1, None),
    Function.MAX: (1, None),
}


# --- Typed AST -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Literal:
    value: int | float | str | bool
    dtype: ColumnType
    position: int


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    dtype: ColumnType
    position: int


@dataclass(frozen=True, slots=True)
class Unary:
    op: str  # "-" or "NOT"
    operand: "Node"
    dtype: ColumnType
    position: int


@dataclass(frozen=True, slots=True)
class Binary:
    op: str  # + - * /  = <> < <= > >=  AND OR
    left: "Node"
    right: "Node"
    dtype: ColumnType
    position: int


@dataclass(frozen=True, slots=True)
class Call:
    function: Function
    args: tuple["Node", ...]
    dtype: ColumnType
    position: int


Node = Literal | Column | Unary | Binary | Call


@dataclass(frozen=True, slots=True)
class CompiledExpression:
    expr: pl.Expr
    dtype: ColumnType
    columns_used: frozenset[str]


# --- Parsing -------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _parser() -> Lark:
    return Lark.open(
        "expressions.lark",
        rel_to=__file__,
        parser="lalr",
        propagate_positions=True,
        maybe_placeholders=False,
    )


def _syntax_error(exc: LarkError, text: str) -> ExpressionError:
    position: int | None
    if isinstance(exc, UnexpectedCharacters):
        position = getattr(exc, "pos_in_stream", None)  # typed None in lark's stubs
    elif isinstance(exc, UnexpectedToken) and exc.token.type != "$END":
        position = exc.token.start_pos if exc.token.start_pos is not None else len(text)
    else:  # UnexpectedEOF, or an unexpected end of input
        position = len(text)
    position = len(text) if position is None else position
    if position >= len(text) or isinstance(exc, UnexpectedEOF):
        return ExpressionError(ErrorCode.SYNTAX, "The expression ends unexpectedly.", len(text))
    return ExpressionError(
        ErrorCode.SYNTAX, f"The expression isn't valid at character {position + 1}.", position
    )


def _start(tree: Tree[Token]) -> int:
    return int(getattr(tree.meta, "start_pos", 0) or 0)


def _check_size(tree: Tree[Token] | Token) -> None:
    """Depth and node limits, checked iteratively before any recursive walk.

    The top node is level 0, so 32 nested parentheses around a value are allowed.
    """
    if not isinstance(tree, Tree):  # pragma: no cover (start always wraps a rule)
        return
    nodes = 0
    stack: list[tuple[Tree[Token], int]] = [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        nodes += 1 + sum(1 for child in node.children if isinstance(child, Token))
        if depth > MAX_DEPTH:
            raise ExpressionError(
                ErrorCode.TOO_DEEP,
                f"The expression is nested more than {MAX_DEPTH} levels deep.",
                _start(node),
            )
        if nodes > MAX_NODES:
            raise ExpressionError(
                ErrorCode.TOO_COMPLEX,
                f"The expression has more than {MAX_NODES} parts.",
                _start(node),
            )
        stack.extend((child, depth + 1) for child in node.children if isinstance(child, Tree))


def parse_expression(text: str, schema: Mapping[str, ColumnType]) -> Node:
    """Parse, check and type ``text`` against ``schema``; raises :class:`ExpressionError`."""
    if len(text) > MAX_LENGTH:
        raise ExpressionError(
            ErrorCode.TOO_LONG,
            f"The expression is longer than {MAX_LENGTH:,} characters.",
            MAX_LENGTH,
        )
    if not text.strip():
        raise ExpressionError(ErrorCode.SYNTAX, "The expression is empty.", 0)
    try:
        tree = _parser().parse(text)
    except LarkError as exc:
        raise _syntax_error(exc, text) from None
    _check_size(tree.children[0])
    return _Builder(schema).build(tree.children[0])


# --- Building the typed AST ------------------------------------------------------------


def _family(dtype: ColumnType) -> str:
    return "number" if dtype in _NUMERIC else dtype.value


def _describe(dtype: ColumnType) -> str:
    return {"number": "a number", "text": "text", "boolean": "true/false", "date": "a date"}[
        _family(dtype)
    ]


def _mismatch(message: str, position: int) -> ExpressionError:
    return ExpressionError(ErrorCode.TYPE_MISMATCH, message, position)


def _numeric_result(*dtypes: ColumnType) -> ColumnType:
    return ColumnType.INTEGER if all(d is ColumnType.INTEGER for d in dtypes) else ColumnType.NUMBER


class _Builder:
    def __init__(self, schema: Mapping[str, ColumnType]) -> None:
        self._schema = {name: ColumnType(dtype) for name, dtype in schema.items()}
        self._folded: dict[str, list[str]] = {}
        for name in self._schema:
            self._folded.setdefault(name.strip().casefold(), []).append(name)
        self._rules: Mapping[str, Callable[[Tree[Token]], Node]] = {
            "number": self._number,
            "string": self._string,
            "true": lambda t: Literal(True, ColumnType.BOOLEAN, _start(t)),
            "false": lambda t: Literal(False, ColumnType.BOOLEAN, _start(t)),
            "bracket_column": self._bracket_column,
            "bare_column": self._bare_column,
            "paren": lambda t: self.build(_trees(t)[0]),
            "neg": self._neg,
            "not_op": self._not,
            "sum": self._arithmetic,
            "product": self._arithmetic,
            "compare": self._compare,
            "and_expr": self._logical,
            "or_expr": self._logical,
            "call": self._call,
        }

    def build(self, tree: Tree[Token] | Token) -> Node:
        if not isinstance(tree, Tree):  # pragma: no cover (every atom is a named rule)
            raise ExpressionError(ErrorCode.SYNTAX, "The expression isn't valid.", 0)
        return self._rules[str(tree.data)](tree)

    # Literals and columns

    def _number(self, tree: Tree[Token]) -> Literal:
        raw = str(_tokens(tree)[0])
        position = _start(tree)
        if _INTEGER_LITERAL.fullmatch(raw):
            value = int(raw)
            if value <= _INT64_MAX:
                return Literal(value, ColumnType.INTEGER, position)
        number = float(raw)
        if number in {float("inf"), float("-inf")}:
            raise ExpressionError(ErrorCode.SYNTAX, "A number is too large.", position)
        return Literal(number, ColumnType.NUMBER, position)

    def _string(self, tree: Tree[Token]) -> Literal:
        raw = str(_tokens(tree)[0])
        return Literal(raw[1:-1].replace('""', '"'), ColumnType.TEXT, _start(tree))

    def _bracket_column(self, tree: Tree[Token]) -> Column:
        raw = str(_tokens(tree)[0])
        return self._column(raw[1:-1].replace("]]", "]"), _start(tree))

    def _bare_column(self, tree: Tree[Token]) -> Column:
        return self._column(str(_tokens(tree)[0]), _start(tree))

    def _column(self, name: str, position: int) -> Column:
        if name in self._schema:
            return Column(name, self._schema[name], position)
        # Forgive case and surrounding spaces when that identifies one column.
        candidates = self._folded.get(name.strip().casefold(), [])
        if len(candidates) == 1:
            return Column(candidates[0], self._schema[candidates[0]], position)
        raise ExpressionError(
            ErrorCode.UNKNOWN_COLUMN, f"There is no column named '{name}'.", position
        )

    # Operators

    def _neg(self, tree: Tree[Token]) -> Node:
        operand = self.build(_trees(tree)[0])
        if operand.dtype not in _NUMERIC:
            raise _mismatch(f"Minus needs a number, not {_describe(operand.dtype)}.", _start(tree))
        if isinstance(operand, Literal) and isinstance(operand.value, int | float):
            # Fold `-5` into a literal so ROUND(x, -2) sees a whole-number literal.
            return Literal(-operand.value, operand.dtype, _start(tree))
        return Unary("-", operand, operand.dtype, _start(tree))

    def _not(self, tree: Tree[Token]) -> Node:
        operand = self.build(_trees(tree)[0])
        if operand.dtype is not ColumnType.BOOLEAN:
            raise _mismatch(f"NOT needs true/false, not {_describe(operand.dtype)}.", _start(tree))
        return Unary("NOT", operand, ColumnType.BOOLEAN, _start(tree))

    def _chain(self, tree: Tree[Token]) -> tuple[Node, list[tuple[Token, Node]]]:
        """``a op b op c`` as ``a`` and ``[(op, b), (op, c)]``."""
        children = tree.children
        first = self.build(children[0])
        rest = [
            (op, self.build(operand))
            for op, operand in zip(children[1::2], children[2::2], strict=True)
            if isinstance(op, Token)
        ]
        return first, rest

    def _arithmetic(self, tree: Tree[Token]) -> Node:
        node, rest = self._chain(tree)
        for op, right in rest:
            for side in (node, right):
                if side.dtype not in _NUMERIC:
                    raise _mismatch(
                        f"'{op}' needs numbers, not {_describe(side.dtype)}.", _pos(op, tree)
                    )
            dtype = (
                ColumnType.NUMBER if str(op) == "/" else _numeric_result(node.dtype, right.dtype)
            )
            node = Binary(str(op), node, right, dtype, _pos(op, tree))
        return node

    def _compare(self, tree: Tree[Token]) -> Node:
        left, [(op, right)] = self._chain(tree)
        if _family(left.dtype) != _family(right.dtype):
            raise _mismatch(
                f"Can't compare {_describe(left.dtype)} with {_describe(right.dtype)}.",
                _pos(op, tree),
            )
        return Binary(str(op), left, right, ColumnType.BOOLEAN, _pos(op, tree))

    def _logical(self, tree: Tree[Token]) -> Node:
        node, rest = self._chain(tree)
        for op, right in rest:
            name = str(op).upper()
            for side in (node, right):
                if side.dtype is not ColumnType.BOOLEAN:
                    raise _mismatch(
                        f"{name} needs true/false, not {_describe(side.dtype)}.", _pos(op, tree)
                    )
            node = Binary(name, node, right, ColumnType.BOOLEAN, _pos(op, tree))
        return node

    # Functions

    def _call(self, tree: Tree[Token]) -> Node:
        name_token = _tokens(tree)[0]
        position = _pos(name_token, tree)
        try:
            function = Function(str(name_token).upper())
        except ValueError:
            available = ", ".join(f.value for f in Function)
            raise ExpressionError(
                ErrorCode.UNKNOWN_FUNCTION,
                f"There is no function named '{name_token}'. Available: {available}.",
                position,
            ) from None
        args = tuple(self.build(child) for child in _trees(tree))
        low, high = _ARITY[function]
        if len(args) < low or (high is not None and len(args) > high):
            expected = (
                f"{low}"
                if low == high
                else f"{low} or more"
                if high is None
                else (f"{low} or {high}")
            )
            raise ExpressionError(
                ErrorCode.SYNTAX,
                f"{function.value} takes {expected} argument{'s' if high != 1 else ''}.",
                position,
            )
        return Call(function, args, self._call_type(function, args, position), position)

    def _call_type(self, function: Function, args: tuple[Node, ...], position: int) -> ColumnType:
        if function is Function.IF:
            condition, then, otherwise = args
            if condition.dtype is not ColumnType.BOOLEAN:
                raise _mismatch(
                    f"IF needs a true/false condition, not {_describe(condition.dtype)}.",
                    position,
                )
            if _family(then.dtype) != _family(otherwise.dtype):
                raise _mismatch(
                    f"IF's two results must have the same type, not {_describe(then.dtype)} "
                    f"and {_describe(otherwise.dtype)}.",
                    position,
                )
            if then.dtype in _NUMERIC:
                return _numeric_result(then.dtype, otherwise.dtype)
            return then.dtype
        for arg in args[:1] if function is Function.ROUND else args:
            if arg.dtype not in _NUMERIC:
                raise _mismatch(
                    f"{function.value} needs numbers, not {_describe(arg.dtype)}.", position
                )
        if function is Function.ROUND:
            if len(args) == 2 and not _whole_number_literal(args[1]):  # noqa: PLR2004
                raise _mismatch(
                    "ROUND's number of digits must be a whole number from "
                    f"-{MAX_ROUND_DIGITS} to {MAX_ROUND_DIGITS}.",
                    position,
                )
            return args[0].dtype
        if function is Function.ABS:
            return args[0].dtype
        return _numeric_result(*(arg.dtype for arg in args))


def _whole_number_literal(node: Node) -> bool:
    return (
        isinstance(node, Literal)
        and node.dtype is ColumnType.INTEGER
        and isinstance(node.value, int)
        and abs(node.value) <= MAX_ROUND_DIGITS
    )


def _trees(tree: Tree[Token]) -> list[Tree[Token]]:
    return [child for child in tree.children if isinstance(child, Tree)]


def _tokens(tree: Tree[Token]) -> list[Token]:
    return [child for child in tree.children if isinstance(child, Token)]


def _pos(token: Token, tree: Tree[Token]) -> int:
    return token.start_pos if token.start_pos is not None else _start(tree)


# --- Compiling to Polars -----------------------------------------------------------------


_PL_DTYPE: Final[Mapping[ColumnType, pl.DataType]] = {
    ColumnType.NUMBER: pl.Float64(),
    ColumnType.INTEGER: pl.Int64(),
    ColumnType.TEXT: pl.String(),
    ColumnType.BOOLEAN: pl.Boolean(),
}

_COMPARISONS: Final[Mapping[str, Callable[[pl.Expr, pl.Expr], pl.Expr]]] = {
    "=": lambda a, b: a == b,
    "<>": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def compile_expression(text: str, schema: Mapping[str, ColumnType]) -> CompiledExpression:
    """Compile ``text`` into a Polars expression over columns of ``schema``."""
    node = parse_expression(text, schema)
    expr = _to_polars(node)
    if node.dtype in _PL_DTYPE:
        expr = expr.cast(_PL_DTYPE[node.dtype])
    return CompiledExpression(expr, node.dtype, frozenset(columns_of(node)))


def columns_of(node: Node) -> set[str]:
    match node:
        case Column(name=name):
            return {name}
        case Literal():
            return set()
        case Unary(operand=operand):
            return columns_of(operand)
        case Binary(left=left, right=right):
            return columns_of(left) | columns_of(right)
        case Call(args=args):
            return set().union(*(columns_of(arg) for arg in args))


def _to_polars(node: Node) -> pl.Expr:  # noqa: PLR0911 (one branch per node kind)
    match node:
        case Literal(value=value, dtype=dtype):
            return pl.lit(value, dtype=_PL_DTYPE[dtype])
        case Column(name=name):
            # by_name matches the name literally (pl.col treats "*" and "^...$" as patterns).
            return cs.by_name(name).as_expr()
        case Unary(op="-", operand=operand):
            return -_to_polars(operand)
        case Unary(operand=operand):
            return ~_to_polars(operand)
        case Binary(op="/", left=left, right=right):
            numerator = _to_polars(left).cast(pl.Float64)
            denominator = _to_polars(right).cast(pl.Float64)
            return (
                pl.when(denominator == 0)
                .then(pl.lit(None, dtype=pl.Float64))
                .otherwise(numerator / denominator)
            )
        case Binary(op=op, left=left, right=right) if op in {"+", "-", "*"}:
            a, b = _numeric_operands(node.dtype, left, right)
            return a + b if op == "+" else a - b if op == "-" else a * b
        case Binary(op="AND", left=left, right=right):
            return _to_polars(left) & _to_polars(right)
        case Binary(op="OR", left=left, right=right):
            return _to_polars(left) | _to_polars(right)
        case Binary(op=op, left=left, right=right):
            if left.dtype in _NUMERIC and right.dtype in _NUMERIC:
                a, b = _numeric_operands(ColumnType.NUMBER, left, right)
            else:
                a, b = _to_polars(left), _to_polars(right)
            return _COMPARISONS[op](a, b)
        case Call():
            return _call_to_polars(node)


def _numeric_operands(dtype: ColumnType, *nodes: Node) -> list[pl.Expr]:
    target = _PL_DTYPE[dtype]
    return [_to_polars(n).cast(target) for n in nodes]


def _call_to_polars(node: Call) -> pl.Expr:
    args = node.args
    match node.function:
        case Function.IF:
            condition = _to_polars(args[0])
            if node.dtype in _NUMERIC:
                then, otherwise = _numeric_operands(node.dtype, args[1], args[2])
            else:
                then, otherwise = _to_polars(args[1]), _to_polars(args[2])
            return (
                pl.when(condition.is_null())
                .then(None)
                .when(condition)
                .then(then)
                .otherwise(otherwise)
            )
        case Function.ROUND:
            return _round(_to_polars(args[0]), args[0].dtype, round_digits(node))
        case Function.ABS:
            return _to_polars(args[0]).abs()
        case Function.MIN:
            return pl.min_horizontal(_numeric_operands(node.dtype, *args))
        case Function.MAX:
            return pl.max_horizontal(_numeric_operands(node.dtype, *args))


def round_digits(node: Call) -> int:
    """The ``n`` of ``ROUND(x, n)`` (0 when omitted); checked to be a literal when typed."""
    if len(node.args) < 2:  # noqa: PLR2004
        return 0
    digits = node.args[1]
    assert isinstance(digits, Literal)  # noqa: S101
    assert isinstance(digits.value, int)  # noqa: S101
    return digits.value


def _round(value: pl.Expr, dtype: ColumnType, digits: int) -> pl.Expr:
    if digits >= 0:
        if dtype is ColumnType.INTEGER:
            return value
        return value.cast(pl.Float64).round(digits, mode="half_away_from_zero")
    scale = 10 ** (-digits)
    rounded: pl.Expr = (value.cast(pl.Float64) / scale).round(0, mode="half_away_from_zero") * scale
    return rounded.cast(pl.Int64) if dtype is ColumnType.INTEGER else rounded
