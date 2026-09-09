import { RANGES, type RangeKey } from "@/lib/range";

// Search and date range, as a plain GET form and plain links.
//
// No client JavaScript: the state lives in the URL, which means a filtered view
// can be bookmarked, shared in a message, and reloaded without going stale.

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
        <input
          type="text"
          name="q"
          defaultValue={q}
          placeholder="Search mechanic ID or WhatsApp number"
          style={{ width: 300 }}
        />
        <button>Search</button>
        {q && (
          <a className="btn" href={`${action}?range=${range}`}>
            Clear
          </a>
        )}
      </form>

      <nav className="ranges">
        {(Object.keys(RANGES) as RangeKey[]).map((key) => (
          <a
            key={key}
            className={key === range ? "range on" : "range"}
            href={`${action}?range=${key}${q ? `&q=${encodeURIComponent(q)}` : ""}`}
          >
            {RANGES[key]}
          </a>
        ))}
      </nav>
    </div>
  );
}
