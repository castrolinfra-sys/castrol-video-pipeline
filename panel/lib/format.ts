// Display helpers.
//
// There is deliberately NO money formatter here. This panel does not show what
// a video cost, which vendor produced it, or which model was used. Duration is
// the metric the client is shown and the one they are billed on, so the only
// numeric formatter is a duration one — if a cost ever reaches a page, it will
// have to arrive as a bare number, which is a visible thing to notice in review.

// Formatters are built ONCE at module scope, not per call. `ts()` runs on every
// row of a 500-row table, and constructing an Intl.DateTimeFormat is one of the
// more expensive things in the standard library.
//
// Locale and timeZone are both pinned. That is what makes the output identical
// on any machine — Vercel runs UTC, and lib/range.ts already exists entirely
// because this project's day boundaries are Indian ones, not the server's.
const DATE_TIME = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const DATE_ONLY = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  day: "2-digit",
  month: "short",
});

/** A timestamp, in IST. Column headers say so; every cell repeating it is noise. */
export function ts(v: string | null | undefined): string {
  if (!v) return "—";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? "—" : DATE_TIME.format(d);
}

/** "15 Sep" — for axis ticks and day rows, where the year is already context. */
export function dayLabel(v: string | null | undefined): string {
  if (!v) return "—";
  // `daily_usage.day` is a bare IST calendar date. Parsing it as UTC midnight
  // and formatting in IST would shift it back a day, so it is split directly.
  const [y, m, d] = String(v).split("-").map(Number);
  if (!y || !m || !d) return String(v);
  return DATE_ONLY.format(new Date(Date.UTC(y, m - 1, d, 12)));
}

/**
 * One video's length. Kept in seconds, because that is the unit of the bill.
 *
 * CEILED to a whole second, never rounded and never shown with a decimal
 * (changed 2026-09-15; this used to print one decimal place). Ceiling is not a
 * cosmetic choice - it is what the vendor does. kie bills per output second and
 * rounds UP, so a 24.2s render is billed as 25s; printing "24.2s" showed a
 * number the client is not charged for and that matches no invoice line.
 * Rounding to nearest would be worse than the decimal, because it would
 * sometimes report LESS than was billed.
 */
export function secs(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "string" ? Number(v) : v;
  return Number.isFinite(n) ? `${Math.ceil(n)}s` : "—";
}

/**
 * A total, in seconds AND in a form a person can hold in their head.
 *
 * Both, not either: seconds is the number that reconciles against an invoice,
 * and "3h 12m" is the number anyone can actually judge as big or small.
 *
 * The gap is a non-breaking space: "3h 12m" wrapping across two lines in a
 * narrow stat card reads as two separate numbers.
 */
export function duration(v: number | string | null | undefined): string {
  const n = typeof v === "string" ? Number(v) : (v ?? 0);
  if (!Number.isFinite(n) || n <= 0) return "0s";
  // Ceil, not round - same reason as secs(): the bill rounds up.
  const total = Math.ceil(n);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

/**
 * The same total, split so it can be SET rather than printed — "4h 12m" as
 * [[4,"h"],[12,"m"]], for the headline treatment where the numerals carry the
 * weight and the units are condensed and tinted.
 */
export function durationParts(
  v: number | string | null | undefined,
): Array<[string, string]> {
  const n = typeof v === "string" ? Number(v) : (v ?? 0);
  if (!Number.isFinite(n) || n <= 0) return [["0", "s"]];
  // Ceil, not round - same reason as secs(): the bill rounds up.
  const total = Math.ceil(n);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return [[String(h), "h"], [String(m), "m"]];
  if (m) return [[String(m), "m"], [String(s), "s"]];
  return [[String(s), "s"]];
}

// Hoisted for the same reason as the date formatters: this runs once per cell.
const NUMBER = new Intl.NumberFormat("en-IN");

export function num(v: number | null | undefined): string {
  return NUMBER.format(v ?? 0);
}

/** "12 videos", kept on one line. */
export function countOf(n: number, singular: string, plural = `${singular}s`): string {
  return `${num(n)} ${n === 1 ? singular : plural}`;
}

export function pct(part: number, whole: number): string {
  if (!whole) return "—";
  return `${((part / whole) * 100).toFixed(1)}%`;
}

export function pill(status: string | null | undefined) {
  return `pill ${(status ?? "").toLowerCase()}`;
}
