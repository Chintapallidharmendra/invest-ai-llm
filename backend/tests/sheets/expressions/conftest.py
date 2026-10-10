"""A shared schema and rows with the edge values the semantics tests need."""

from datetime import date
from typing import Any, Final

import polars as pl

from app.sheets.expressions import ColumnType

SCHEMA: Final = {
    "Revenue": ColumnType.NUMBER,
    "Cost": ColumnType.NUMBER,
    "Units": ColumnType.INTEGER,
    "Zero": ColumnType.INTEGER,
    "Region": ColumnType.TEXT,
    "Active": ColumnType.BOOLEAN,
    "Margin %": ColumnType.NUMBER,
    "Day": ColumnType.DATE,
    "Day2": ColumnType.DATE,
    "Ünïcode name": ColumnType.NUMBER,
    "a]b": ColumnType.NUMBER,
    "*": ColumnType.INTEGER,
}

_PL: Final = {
    ColumnType.NUMBER: pl.Float64,
    ColumnType.INTEGER: pl.Int64,
    ColumnType.TEXT: pl.String,
    ColumnType.BOOLEAN: pl.Boolean,
    ColumnType.DATE: pl.Date,
}

ROWS: Final[list[dict[str, Any]]] = [
    {"Revenue": 100.0, "Cost": 40.0, "Units": 7, "Zero": 0, "Region": "North",
     "Active": True, "Margin %": 2.5, "Day": date(2026, 1, 1), "Day2": date(2026, 3, 1),
     "Ünïcode name": 1.5, "a]b": 2.0, "*": 3},
    {"Revenue": 0.0, "Cost": 0.0, "Units": 0, "Zero": 0, "Region": "South",
     "Active": False, "Margin %": -2.5, "Day": date(2026, 5, 1), "Day2": date(2026, 3, 1),
     "Ünïcode name": -1.5, "a]b": 0.0, "*": 0},
    {"Revenue": None, "Cost": 12.5, "Units": None, "Zero": 0, "Region": None,
     "Active": None, "Margin %": None, "Day": None, "Day2": date(2026, 3, 1),
     "Ünïcode name": None, "a]b": None, "*": None},
    {"Revenue": -250.75, "Cost": 1e-9, "Units": -3, "Zero": 0, "Region": "north",
     "Active": True, "Margin %": 2.675, "Day": date(2026, 3, 1), "Day2": date(2026, 3, 1),
     "Ünïcode name": 0.5, "a]b": -7.25, "*": -1},
    {"Revenue": 125_000_000_000.0, "Cost": 98_765_432_100.5, "Units": 1_000_000,
     "Zero": 0, "Region": "", "Active": False, "Margin %": 0.005, "Day": date(2025, 12, 31),
     "Day2": date(2026, 3, 1), "Ünïcode name": 99.995, "a]b": 1.0, "*": 12},
    {"Revenue": 1.0, "Cost": 3.0, "Units": 2, "Zero": 0, "Region": 'Say "hi"',
     "Active": True, "Margin %": 15.0, "Day": date(2026, 3, 1), "Day2": date(2026, 3, 2),
     "Ünïcode name": -0.5, "a]b": 3.0, "*": 5},
]  # fmt: skip


def frame() -> pl.DataFrame:
    return pl.DataFrame(ROWS, schema={name: _PL[dtype] for name, dtype in SCHEMA.items()})
