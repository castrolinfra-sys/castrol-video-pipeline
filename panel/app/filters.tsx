import Link from "next/link";
import { RANGES, type RangeKey } from "@/lib/range";

// Search and date range, as a plain GET form and prefetched links.
//
// No client JavaScript: the state lives in the URL, which means a filtered view
// can be bookmarked, shared in a message, and reloaded without going stale.
//
// The range chips are `Link` rather than `<a>` so changing a filter is a client
// transition into the route's loading skeleton, not a full document reload.
// They carry aria-current, because "which range am I on" was previously
// signalled by border colour alone.

export function Filters({
  action,
  q,
  range,
}: {
  action: string;
  q: string;
  range: RangeKey;
}) {
  return (
    <div className="filters">
      <form method="get" action={action}>
        <input type="hidden" name="range" value={range} />
        <div className="field">
          <label htmlFor="q">Search</label>
          <input
            id="q"
            type="text"
            name="q"
            defaultValue={q}
            placeholder="Mechanic ID or WhatsApp number…"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <button type="submit">Search</button>
        {q && (
          <Link className="btn" href={`${action}?range=${range}`}>
            Clear
          </Link>
        )}
      </form>

      <nav className="ranges" aria-label="Date range">
        {(Object.keys(RANGES) as RangeKey[]).map((key) => (
          <Link
            key={key}
            className="range"
            aria-current={key === range ? "true" : undefined}
            href={`${action}?range=${key}${q ? `&q=${encodeURIComponent(q)}` : ""}`}
          >
            {RANGES[key]}
          </Link>
        ))}
      </nav>
    </div>
  );
}
