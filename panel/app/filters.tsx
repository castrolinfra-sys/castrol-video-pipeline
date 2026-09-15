import Link from "next/link";
import { LinkPending } from "./pending";
import { RANGES, type RangeKey } from "@/lib/range";

// Search and date range, as a plain GET form and prefetched links.
//
// No client JavaScript: the state lives in the URL, which means a filtered view
// can be bookmarked, shared in a message, and reloaded without going stale.
//
// The range chips are `Link` rather than `<a>`, so changing a filter is a client
// transition rather than a full document reload. They carry aria-current,
// because "which range am I on" was previously signalled by border colour alone.
//
// prefetch={false} on both, and that is measured rather than cautious. These
// links change only the query string, so they re-render the SAME route segment:
// the prefetched loading shell is never used (the skeleton does not mount for a
// searchParams-only navigation) and the click refetches the page in full
// regardless. What the prefetch DID do was fire one RSC request per chip on
// every page load - six of them - and each one runs middleware, which means a
// getUser() round trip to Supabase Auth apiece. Six auth calls to fill a cache
// nothing reads.
//
// Since there is no loading skeleton for this navigation, LinkPending supplies
// the feedback the browser used to supply for free.

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
          <Link className="btn" prefetch={false} href={`${action}?range=${range}`}>
            <LinkPending>Clear</LinkPending>
          </Link>
        )}
      </form>

      <nav className="ranges" aria-label="Date range">
        {(Object.keys(RANGES) as RangeKey[]).map((key) => (
          <Link
            key={key}
            className="range"
            prefetch={false}
            aria-current={key === range ? "true" : undefined}
            href={`${action}?range=${key}${q ? `&q=${encodeURIComponent(q)}` : ""}`}
          >
            <LinkPending>{RANGES[key]}</LinkPending>
          </Link>
        ))}
      </nav>
    </div>
  );
}
