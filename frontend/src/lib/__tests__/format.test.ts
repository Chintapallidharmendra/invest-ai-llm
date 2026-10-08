import { describe, expect, it } from "vitest";

import {
  formatCrore,
  formatDate,
  formatDateTime,
  formatINR,
  formatINRCompact,
  formatIndian,
  formatInternational,
  formatLakh,
  formatPercent,
  PLACEHOLDER,
  rupeesToCrore,
  rupeesToLakh,
} from "@/lib/format";

describe("dates in IST", () => {
  it.each([
    // UTC evening crosses midnight into the next IST day.
    ["2026-10-06T18:29:59Z", "06-Oct-2026", "06-Oct-2026 23:59 IST"],
    ["2026-10-06T18:30:00Z", "07-Oct-2026", "07-Oct-2026 00:00 IST"],
    ["2026-12-31T19:00:00Z", "01-Jan-2027", "01-Jan-2027 00:30 IST"],
    ["2026-09-01T09:15:00Z", "01-Sep-2026", "01-Sep-2026 14:45 IST"],
    ["2024-02-28T20:00:00Z", "29-Feb-2024", "29-Feb-2024 01:30 IST"],
    [Date.UTC(2026, 0, 5, 3, 4), "05-Jan-2026", "05-Jan-2026 08:34 IST"],
  ])("%s → %s / %s", (input, date, dateTime) => {
    expect(formatDate(input)).toBe(date);
    expect(formatDateTime(input)).toBe(dateTime);
  });

  it("accepts Date objects and rejects bad input", () => {
    expect(formatDate(new Date("2026-10-06T00:00:00Z"))).toBe("06-Oct-2026");
    expect(formatDate("not a date")).toBe(PLACEHOLDER);
    expect(formatDateTime(null)).toBe(PLACEHOLDER);
    expect(formatDate(undefined)).toBe(PLACEHOLDER);
    expect(formatDate("")).toBe(PLACEHOLDER);
  });
});

describe("number grouping", () => {
  it.each([
    [100000, "1,00,000", "100,000"],
    [1234567.5, "12,34,567.5", "1,234,567.5"],
    [999, "999", "999"],
    [10000000, "1,00,00,000", "10,000,000"],
    [-250000, "-2,50,000", "-250,000"],
    [0.125, "0.13", "0.13"],
    [-0.001, "0", "0"],
  ])("%d → %s (IN) / %s (intl)", (value, indian, intl) => {
    expect(formatIndian(value)).toBe(indian);
    expect(formatInternational(value)).toBe(intl);
  });

  it("supports fixed decimals and rejects non-finite values", () => {
    expect(formatIndian(100000, { decimals: 2 })).toBe("1,00,000.00");
    expect(formatInternational(1.5, { decimals: 0 })).toBe("2");
    expect(formatIndian(1.23456, { maxDecimals: 4 })).toBe("1.2346");
    expect(formatIndian(Number.NaN)).toBe(PLACEHOLDER);
    expect(formatInternational(Number.POSITIVE_INFINITY)).toBe(PLACEHOLDER);
    expect(formatIndian(null)).toBe(PLACEHOLDER);
  });
});

describe("rupees", () => {
  it.each([
    [() => formatCrore(4215.5), "₹4,215.5 cr"],
    [() => formatCrore(4215.5, { decimals: 2 }), "₹4,215.50 cr"],
    [() => formatCrore(-12.3), "-₹12.3 cr"],
    [() => formatCrore(-0.001), "₹0 cr"],
    [() => formatLakh(12.5), "₹12.5 lakh"],
    [() => formatINR(100000), "₹1,00,000"],
    [() => formatINRCompact(42_155_000_000), "₹4,215.5 cr"],
    [() => formatINRCompact(1_250_000), "₹12.5 lakh"],
    [() => formatINRCompact(-99_999), "-₹99,999"],
    [() => formatINR(undefined), PLACEHOLDER],
  ])("%#: %s", (fn, expected) => {
    expect(fn()).toBe(expected);
  });

  it("converts rupees to lakh and crore", () => {
    expect(rupeesToLakh(1_250_000)).toBe(12.5);
    expect(rupeesToCrore(42_155_000_000)).toBe(4215.5);
  });
});

describe("percent", () => {
  it.each([
    [12.345, 2, "12.35%"],
    [12.3, 2, "12.30%"],
    [7, 1, "7.0%"],
    [-3.456, 1, "-3.5%"],
    [-0.04, 1, "0.0%"],
    [0, 0, "0%"],
  ])("%d with %d decimals → %s", (value, decimals, expected) => {
    expect(formatPercent(value, decimals)).toBe(expected);
  });

  it("defaults to two decimals", () => {
    expect(formatPercent(5)).toBe("5.00%");
    expect(formatPercent(Number.NaN)).toBe(PLACEHOLDER);
  });
});
