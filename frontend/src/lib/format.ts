/**
 * Display formatting (ADR-003): the API sends ISO-8601 UTC and plain JSON numbers;
 * only the frontend turns them into IST dates and Indian/international figures.
 *
 * IST is a fixed UTC+05:30 with no DST, so dates are computed arithmetically rather
 * than through Intl month names (some ICU builds print "Sept").
 */

/** Shown for missing, invalid or non-finite values. */
export const PLACEHOLDER = "—";

const IST_OFFSET_MS = (5 * 60 + 30) * 60 * 1000;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;

export type DateInput = string | number | Date | null | undefined;

function toIstParts(value: DateInput) {
  if (value === null || value === undefined || value === "") return undefined;
  const date = value instanceof Date ? value : new Date(value);
  const ms = date.getTime();
  if (Number.isNaN(ms)) return undefined;
  const ist = new Date(ms + IST_OFFSET_MS);
  return {
    day: String(ist.getUTCDate()).padStart(2, "0"),
    month: MONTHS[ist.getUTCMonth()] ?? "",
    year: String(ist.getUTCFullYear()),
    hours: String(ist.getUTCHours()).padStart(2, "0"),
    minutes: String(ist.getUTCMinutes()).padStart(2, "0"),
  };
}

/** `06-Oct-2026` (the IST calendar date). */
export function formatDate(value: DateInput): string {
  const p = toIstParts(value);
  return p ? `${p.day}-${p.month}-${p.year}` : PLACEHOLDER;
}

/** `06-Oct-2026 14:45 IST` (24-hour clock). */
export function formatDateTime(value: DateInput): string {
  const p = toIstParts(value);
  return p ? `${p.day}-${p.month}-${p.year} ${p.hours}:${p.minutes} IST` : PLACEHOLDER;
}

export interface NumberFormatOptions {
  /** Fixed number of decimals. Default: up to `maxDecimals`, trailing zeros dropped. */
  decimals?: number;
  /** Used when `decimals` is not set. Default 2. */
  maxDecimals?: number;
}

const formatters = new Map<string, Intl.NumberFormat>();

function numberFormat(locale: string, min: number, max: number): Intl.NumberFormat {
  const key = `${locale}|${String(min)}|${String(max)}`;
  let fmt = formatters.get(key);
  if (!fmt) {
    fmt = new Intl.NumberFormat(locale, {
      minimumFractionDigits: min,
      maximumFractionDigits: max,
      useGrouping: true,
    });
    formatters.set(key, fmt);
  }
  return fmt;
}

function isNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function grouped(locale: string, value: number, options: NumberFormatOptions): string {
  const { decimals, maxDecimals = 2 } = options;
  const min = decimals ?? 0;
  const max = decimals ?? maxDecimals;
  const out = numberFormat(locale, min, max).format(value);
  // Avoid "-0" / "-0.00" after rounding.
  return /^-0(?:\.0+)?$/.test(out) ? out.slice(1) : out;
}

/** Indian grouping: `1,00,000`, `12,34,567.5`. */
export function formatIndian(value: number | null | undefined, options: NumberFormatOptions = {}): string {
  return isNumber(value) ? grouped("en-IN", value, options) : PLACEHOLDER;
}

/** International grouping: `100,000`, `1,234,567.5`. */
export function formatInternational(
  value: number | null | undefined,
  options: NumberFormatOptions = {},
): string {
  return isNumber(value) ? grouped("en-US", value, options) : PLACEHOLDER;
}

export const RUPEES_PER_LAKH = 1e5;
export const RUPEES_PER_CRORE = 1e7;

export const rupeesToLakh = (rupees: number): number => rupees / RUPEES_PER_LAKH;
export const rupeesToCrore = (rupees: number): number => rupees / RUPEES_PER_CRORE;

function rupee(value: number, options: NumberFormatOptions, suffix: string): string {
  const body = grouped("en-IN", Math.abs(value), options);
  const sign = value < 0 && !/^0(?:\.0+)?$/.test(body) ? "-" : "";
  return `${sign}₹${body}${suffix}`;
}

/** Rupees with Indian grouping: `₹1,00,000`. */
export function formatINR(rupees: number | null | undefined, options: NumberFormatOptions = {}): string {
  return isNumber(rupees) ? rupee(rupees, options, "") : PLACEHOLDER;
}

/** A value already in crore (API unit `INR_crore`): `₹4,215.5 cr`. */
export function formatCrore(crore: number | null | undefined, options: NumberFormatOptions = {}): string {
  return isNumber(crore) ? rupee(crore, options, " cr") : PLACEHOLDER;
}

/** A value already in lakh: `₹12.5 lakh`. */
export function formatLakh(lakh: number | null | undefined, options: NumberFormatOptions = {}): string {
  return isNumber(lakh) ? rupee(lakh, options, " lakh") : PLACEHOLDER;
}

/** Rupees scaled to crore (>= 1 cr) or lakh (>= 1 lakh), else plain rupees. */
export function formatINRCompact(rupees: number | null | undefined, options: NumberFormatOptions = {}): string {
  if (!isNumber(rupees)) return PLACEHOLDER;
  const abs = Math.abs(rupees);
  if (abs >= RUPEES_PER_CRORE) return rupee(rupeesToCrore(rupees), options, " cr");
  if (abs >= RUPEES_PER_LAKH) return rupee(rupeesToLakh(rupees), options, " lakh");
  return rupee(rupees, options, "");
}

/**
 * A value in percentage points (API unit `pct`): `formatPercent(12.345)` → `12.35%`.
 * Always prints exactly `decimals` digits (default 2).
 */
export function formatPercent(value: number | null | undefined, decimals = 2): string {
  return isNumber(value) ? `${grouped("en-IN", value, { decimals })}%` : PLACEHOLDER;
}
