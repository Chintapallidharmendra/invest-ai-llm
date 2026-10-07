"""Fabricated company financials, shared by the CIMs, workbooks and deck so the same
company's figures agree across documents.

Amounts are integers in tenths of a crore (INR), so totals are exact sums of their
printed parts.
"""

from dataclasses import dataclass, field
from typing import Final

from generate.content import Company, rng_for

HISTORY: Final = ("FY2020", "FY2021", "FY2022", "FY2023", "FY2024")
PROJECTION: Final = ("FY2025E", "FY2026E", "FY2027E", "FY2028E", "FY2029E")
YEARS: Final = HISTORY + PROJECTION
MONTHS: Final = ("Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar")


def fmt(tenths: int) -> str:
    """1234.5 crore -> '1,234.5'; negatives in parentheses."""
    text = f"{abs(tenths) / 10:,.1f}"
    return f"({text})" if tenths < 0 else text


def fmt_inr(tenths: int) -> str:
    """Indian digit grouping with the rupee sign, e.g. '₹1,23,456.7'."""
    whole, frac = divmod(abs(tenths), 10)
    digits = str(whole)
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    grouped = ",".join([*groups, tail]) if groups else tail
    return f"{'-' if tenths < 0 else ''}₹{grouped}.{frac}"


@dataclass
class Financials:
    segments: dict[str, list[int]]
    cogs: list[int]
    employee: list[int]
    other_opex: list[int]
    da: list[int]
    interest: list[int]
    tax: list[int]
    net_debt: list[int]
    employees: list[int]
    kpi: list[int]  # sector KPI, in tenths of its unit
    monthly: dict[str, list[int]] = field(default_factory=dict)  # segment -> 60 months

    def revenue(self, i: int) -> int:
        return sum(s[i] for s in self.segments.values())

    def gross_profit(self, i: int) -> int:
        return self.revenue(i) - self.cogs[i]

    def ebitda(self, i: int) -> int:
        return self.gross_profit(i) - self.employee[i] - self.other_opex[i]

    def ebit(self, i: int) -> int:
        return self.ebitda(i) - self.da[i]

    def pbt(self, i: int) -> int:
        return self.ebit(i) - self.interest[i]

    def pat(self, i: int) -> int:
        return self.pbt(i) - self.tax[i]

    def margin_bp(self, i: int) -> int:
        """EBITDA margin in basis points."""
        return round(self.ebitda(i) * 10000 / self.revenue(i))

    def pl_rows(self) -> list[tuple[str, list[int]]]:
        n = len(YEARS)
        return [
            *[(f"Revenue - {name}", values) for name, values in self.segments.items()],
            ("Total revenue", [self.revenue(i) for i in range(n)]),
            ("Cost of goods sold", self.cogs),
            ("Gross profit", [self.gross_profit(i) for i in range(n)]),
            ("Employee costs", self.employee),
            ("Other operating expenses", self.other_opex),
            ("EBITDA", [self.ebitda(i) for i in range(n)]),
            ("Depreciation and amortisation", self.da),
            ("EBIT", [self.ebit(i) for i in range(n)]),
            ("Finance costs", self.interest),
            ("Profit before tax", [self.pbt(i) for i in range(n)]),
            ("Tax", self.tax),
            ("Profit after tax", [self.pat(i) for i in range(n)]),
        ]


def make_financials(company: Company, base_revenue_cr: int) -> Financials:
    rng = rng_for("financials", company.short)
    n = len(YEARS)
    shares = [rng.randint(25, 45) for _ in company.sector.segments]
    segments: dict[str, list[int]] = {}
    for name, share in zip(company.sector.segments, shares, strict=True):
        value = base_revenue_cr * 10 * share // sum(shares)
        series = []
        for _ in range(n):
            series.append(value)
            value = value * (100 + rng.randint(8, 24)) // 100
        segments[name] = series
    revenue = [sum(s[i] for s in segments.values()) for i in range(n)]
    cogs = [r * rng.randint(48, 56) // 100 for r in revenue]
    employee = [r * rng.randint(9, 12) // 100 for r in revenue]
    other = [r * rng.randint(10, 14) // 100 for r in revenue]
    da = [r * rng.randint(3, 5) // 100 for r in revenue]
    interest = [r * rng.randint(1, 3) // 100 for r in revenue]
    fin = Financials(segments, cogs, employee, other, da, interest, [], [], [], [])
    fin.tax = [max(fin.pbt(i), 0) * 25 // 100 for i in range(n)]
    fin.net_debt = [r * rng.randint(15, 35) // 100 for r in revenue]
    fin.employees = [rng.randint(1500, 2500) + i * rng.randint(150, 400) for i in range(n)]
    fin.kpi = [rng.randint(600, 900) + i * rng.randint(5, 25) for i in range(n)]
    for name, series in segments.items():
        months: list[int] = []
        for year_total in series[: len(HISTORY)]:
            weights = [rng.randint(80, 120) for _ in MONTHS]
            parts = [year_total * w // sum(weights) for w in weights]
            parts[-1] += year_total - sum(parts)
            months.extend(parts)
        fin.monthly[name] = months
    return fin
