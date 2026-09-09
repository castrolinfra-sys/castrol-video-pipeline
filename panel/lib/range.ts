// Date windows, on the client's calendar.
//
// India has no daylight saving, so the offset is a constant and this needs no
// timezone library. It does need to be IST rather than the server's clock:
// Vercel runs UTC, so a job made at 02:00 IST is still "yesterday" in UTC and
// would drop out of a "today" filter that the client can plainly see is wrong.

const IST_OFFSET_MS = 5.5 * 60 * 60 * 1000;

/** The UTC instant of midnight, `daysAgo` days back, in IST. */
function istMidnight(daysAgo: number): Date {
  const nowInIst = new Date(Date.now() + IST_OFFSET_MS);
  return new Date(
    Date.UTC(
      nowInIst.getUTCFullYear(),
      nowInIst.getUTCMonth(),
      nowInIst.getUTCDate() - daysAgo,
    ) - IST_OFFSET_MS,
  );
}

/** The IST calendar date `daysAgo` days back, as YYYY-MM-DD.
 *
 * `daily_usage.day` is already an IST date, so period totals are computed by
 * comparing these strings rather than by re-deriving timezones per row. */
export function istDay(daysAgo: number): string {
  // istMidnight returns a UTC instant; adding the offset back shifts the
  // wall-clock reading to IST, so the date part of the ISO string is the
  // Indian calendar date rather than the UTC one.
  return new Date(istMidnight(daysAgo).getTime() + IST_OFFSET_MS)
    .toISOString()
    .slice(0, 10);
}

export const RANGES = {
  today: "Today",
  yesterday: "Yesterday",
  "2d": "2 days",
  "7d": "7 days",
  "30d": "30 days",
  all: "All time",
} as const;

export type RangeKey = keyof typeof RANGES;

export function isRange(v: string | undefined): v is RangeKey {
  return !!v && v in RANGES;
}

/**
 * `{ from, to }` as ISO strings, either side optional.
 *
 * "Yesterday" is the only bounded one — it has an end as well as a start.
 * The rest are "since", which is what someone means by "last 7 days": today
 * included, not the seven days before today.
 */
export function bounds(range: RangeKey): { from?: string; to?: string } {
  switch (range) {
    case "today":
      return { from: istMidnight(0).toISOString() };
    case "yesterday":
      return { from: istMidnight(1).toISOString(), to: istMidnight(0).toISOString() };
    case "2d":
      return { from: istMidnight(1).toISOString() };
    case "7d":
      return { from: istMidnight(6).toISOString() };
    case "30d":
      return { from: istMidnight(29).toISOString() };
    case "all":
      return {};
  }
}
