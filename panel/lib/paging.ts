// Pagination, shared by every table page.
//
// Offset pages, not keyset. Keyset is the textbook answer for large tables
// because an insert between two page loads can shift a row onto the next page,
// but it cannot jump to "page 4" and it makes Previous awkward - and at ~40
// jobs a day, rows arriving while someone reads page 3 is not a problem worth
// that cost. Page 1 is always the newest, which is where people look.
//
// The total comes from `{ count: "exact" }` on the SAME query that fetches the
// page, so it is one round trip. That count used to be refused (it printed a
// number nobody acted on); with pages it is what says how many there are.

export const PAGE_SIZE = 100;

/** `?page=` as a 1-based page number. Anything unparseable is page 1. */
export function parsePage(v: string | undefined): number {
  const n = Number.parseInt(v ?? "", 10);
  return Number.isFinite(n) && n >= 1 ? n : 1;
}

/** Inclusive row bounds for `.range(from, to)`. */
export function pageBounds(page: number): { from: number; to: number } {
  const from = (page - 1) * PAGE_SIZE;
  return { from, to: from + PAGE_SIZE - 1 };
}

export function pageCount(total: number): number {
  return Math.max(1, Math.ceil(total / PAGE_SIZE));
}

/**
 * PostgREST answers a range that starts past the last row with an ERROR
 * (416, code PGRST103), not with an empty page. A stale bookmark to page 7 of
 * what is now 5 pages would otherwise render as "could not load" - the pages
 * treat this as "past the end" instead.
 */
export function isPastEnd(error: { code?: string } | null): boolean {
  return error?.code === "PGRST103";
}
