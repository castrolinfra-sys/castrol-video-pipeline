export function ts(v: string | null | undefined): string {
  if (!v) return "—";
  const d = new Date(v);
  return d.toISOString().replace("T", " ").slice(0, 19) + "Z";
}

export function usd(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "string" ? Number(v) : v;
  return Number.isFinite(n) ? `$${n.toFixed(4)}` : "—";
}

export function pill(status: string | null | undefined) {
  return `pill ${(status ?? "").toLowerCase()}`;
}
