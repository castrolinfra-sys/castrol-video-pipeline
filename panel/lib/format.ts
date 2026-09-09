// Display helpers.
//
// There is deliberately NO money formatter here. This panel does not show what
// a video cost, which vendor produced it, or which model was used. Duration is
// the metric the client is shown and the one they are billed on, so the only
// numeric formatter is a duration one — if a cost ever reaches a page, it will
// have to arrive as a bare number, which is a visible thing to notice in review.

export function ts(v: string | null | undefined): string {
  if (!v) return "—";
  const d = new Date(v);
  return d.toISOString().replace("T", " ").slice(0, 19) + "Z";
}

/** One video's length. Kept in seconds, because that is the unit of the bill. */
export function secs(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "string" ? Number(v) : v;
  return Number.isFinite(n) ? `${n.toFixed(1)}s` : "—";
}

/**
 * A total, in seconds AND in a form a person can hold in their head.
 *
 * Both, not either: seconds is the number that reconciles against an invoice,
 * and "3h 12m" is the number anyone can actually judge as big or small.
 */
export function duration(v: number | string | null | undefined): string {
  const n = typeof v === "string" ? Number(v) : (v ?? 0);
  if (!Number.isFinite(n) || n <= 0) return "0s";
  const total = Math.round(n);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

export function num(v: number | null | undefined): string {
  return (v ?? 0).toLocaleString("en-IN");
}

export function pct(part: number, whole: number): string {
  if (!whole) return "—";
  return `${((part / whole) * 100).toFixed(1)}%`;
}

export function pill(status: string | null | undefined) {
  return `pill ${(status ?? "").toLowerCase()}`;
}
