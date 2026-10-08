"""Result and input types of the grounding library (FR-036, ADR-025)."""

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from app.guardrails.grounding.normalise import Dimension, date_iso, index_key


class Unit(StrEnum):
    """What a figure's ``value`` measures.

    - ``INR``/``USD``/``EUR``: amounts in the currency's base unit (rupees, dollars,
      euros), scale words applied: "₹4,215 cr" has value 42,150,000,000.
    - ``PCT``: percent ("12.5%" is 12.5). ``BPS``: basis points ("25 bps" is 25).
    - ``MULTIPLE``: "5.2x" is 5.2.
    - ``COUNT``: a non-negative integer written without unit or scale.
    - ``PLAIN``: any other unit-less number, scale words applied ("4,215 crore",
      "1.2", "(1,234)").
    - ``DATE``: ``YYYYMMDD`` as a number, with ``00`` for parts not written (see
      :attr:`Figure.iso`).
    """

    INR = "INR"
    USD = "USD"
    EUR = "EUR"
    PCT = "PCT"
    BPS = "BPS"
    MULTIPLE = "MULTIPLE"
    COUNT = "COUNT"
    PLAIN = "PLAIN"
    DATE = "DATE"


class FigureKind(StrEnum):
    NUMBER = "number"  # an amount, ratio, multiple or count (possibly one end of a range)
    YEAR = "year"  # a bare year, "2025"
    DATE = "date"  # a calendar date or month: "06-Oct-2026", "March 2025"
    PERIOD = "period"  # a period label or duration: "FY26", "Q2 FY26", "H1", "3Y"
    ORDINAL = "ordinal"  # "3rd", "21st"
    LIST_MARKER = "list_marker"  # "1." / "2)" / "(3)" opening a line
    LOCATOR = "locator"  # page, slide, section, footnote or cell reference


@dataclass(frozen=True, slots=True)
class Figure:
    """One number in a text.

    ``step`` is the display resolution in ``value``'s units ("4,215 cr" has a step of
    1 crore; "4,215.37 cr" of 0.01 crore); matching accepts any source value that rounds
    to this figure at that step. ``number`` is the numeral as written, before scale words
    ("₹4,215 cr" has number 4215). ``signed`` is true for an explicit minus or
    accounting parentheses. Both ends of a range ("10-12%") share ``range_span``.
    """

    span: tuple[int, int]
    raw: str
    value: Decimal
    unit: Unit
    kind: FigureKind = FigureKind.NUMBER
    step: Decimal = Decimal(1)
    number: Decimal | None = None
    signed: bool = False
    range_span: tuple[int, int] | None = None

    @property
    def iso(self) -> str | None:
        """ISO form of a date or year ("2026-10-06", "2025-03", "2025"); else ``None``."""
        if self.kind in (FigureKind.DATE, FigureKind.YEAR):
            return date_iso(self.value)
        return None


# --- Grounding set ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GroundedValue:
    """A canonical value (same conventions as :class:`Figure`) and where it came from."""

    value: Decimal
    unit: Unit
    origin: str
    raw: str = ""


class GroundingSet:
    """The values an answer may use, indexed for interval lookups.

    Build it with :func:`app.guardrails.grounding.build_grounding_set`; it is immutable.
    """

    __slots__ = ("_entries", "_index")

    def __init__(self, entries: Iterable[GroundedValue] = ()) -> None:
        self._entries = tuple(entries)
        buckets: dict[Dimension, list[tuple[Decimal, int]]] = {}
        for i, entry in enumerate(self._entries):
            dim, key = index_key(entry.unit, entry.value)
            buckets.setdefault(dim, []).append((key, i))
        self._index: dict[Dimension, tuple[list[Decimal], list[GroundedValue]]] = {}
        for dim, items in buckets.items():
            items.sort()
            self._index[dim] = ([k for k, _ in items], [self._entries[i] for _, i in items])

    @property
    def entries(self) -> tuple[GroundedValue, ...]:
        return self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[GroundedValue]:
        return iter(self._entries)

    def __or__(self, other: "GroundingSet") -> "GroundingSet":
        return GroundingSet((*self._entries, *other._entries))

    def dimensions(self) -> frozenset[Dimension]:
        return frozenset(self._index)

    def find(
        self,
        dim: Dimension,
        lo: Decimal,
        hi: Decimal,
        *,
        include_lo: bool = True,
        include_hi: bool = False,
    ) -> list[GroundedValue]:
        """Entries of ``dim`` whose index key lies between ``lo`` and ``hi``."""
        bucket = self._index.get(dim)
        if bucket is None:
            return []
        keys, entries = bucket
        start = bisect_left(keys, lo) if include_lo else bisect_right(keys, lo)
        stop = bisect_right(keys, hi) if include_hi else bisect_left(keys, hi)
        return entries[start:stop]


# --- Sources ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceText:
    """Text whose figures ground an answer: a retrieved chunk, the user's message."""

    text: str
    origin: str


@dataclass(frozen=True, slots=True)
class TableCell:
    """One table or sheet cell. ``unit``/``scale`` are the table's metadata ("₹ crore",
    "%"), applied when the cell text carries none of its own."""

    value: str | Decimal | int
    origin: str
    unit: Unit | None = None
    scale: str | Decimal | None = None


@dataclass(frozen=True, slots=True)
class ExplicitValue:
    """A value from the calculation engine or an operation, or the current date."""

    value: Decimal | int | str | date
    unit: Unit
    origin: str


Source = SourceText | TableCell | ExplicitValue


class MatchReason(StrEnum):
    MATCHED = "matched"
    EXEMPT = "exempt"
    NOT_FOUND = "not_found"
    # The value is in the set, but in another currency (never a match).
    CROSS_CURRENCY = "cross_currency"


@dataclass(frozen=True, slots=True)
class MatchResult:
    figure: Figure
    reason: MatchReason
    origins: tuple[str, ...] = ()

    @property
    def matched(self) -> bool:
        return self.reason is MatchReason.MATCHED

    @property
    def grounded(self) -> bool:
        """Matched or exempt: the figure may be shown."""
        return self.reason in (MatchReason.MATCHED, MatchReason.EXEMPT)
