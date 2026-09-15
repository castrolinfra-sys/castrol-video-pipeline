// Loading skeletons.
//
// Every page in this panel is `force-dynamic` against Supabase, so there is
// always a real wait before anything can render. Without a skeleton the old
// page simply sat there — indistinguishable from a click that did not land.
//
// The bars are decorative and hidden from assistive tech; the announcement is
// the single visually-hidden "Loading…" line, so a screen reader hears one
// status rather than forty empty cells.

const WIDTHS = ["72%", "54%", "88%", "63%", "45%", "80%", "58%", "69%"];

export function Bar({ width = "72%" }: { width?: string }) {
  return <span className="sk" style={{ width }} aria-hidden="true" />;
}

export function Loading({ children }: { children: React.ReactNode }) {
  return (
    <div aria-busy="true">
      <span className="visually-hidden" role="status">
        Loading…
      </span>
      {children}
    </div>
  );
}

/**
 * Just the rows.
 *
 * Split out from TableSkeleton because the two callers need different things
 * above the table. A route-level `loading.tsx` replaces the entire page, so it
 * wants the heading and filter placeholders too. A Suspense boundary INSIDE a
 * page keeps the real filter chips mounted and swaps only the table, so it must
 * not draw a second set.
 */
export function SkeletonRows({ cols, rows = 12 }: { cols: number; rows?: number }) {
  return (
    <div className="scroll">
      <table>
        <tbody>
          {Array.from({ length: rows }, (_, r) => (
            <tr key={r}>
              {Array.from({ length: cols }, (_, c) => (
                <td key={c}>
                  <Bar width={WIDTHS[(r + c) % WIDTHS.length]} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A table-shaped placeholder: heading, optional filter bar, then rows. */
export function TableSkeleton({
  cols,
  rows = 12,
  filters = false,
}: {
  cols: number;
  rows?: number;
  filters?: boolean;
}) {
  return (
    <Loading>
      {/* Same wrapper as the real PageHead, so the table below does not shift
          upward when the content lands. */}
      <div className="page-head">
        <Bar width="120px" />
        <Bar width="240px" />
      </div>

      {filters && (
        <div className="filters">
          <Bar width="360px" />
          <span className="ranges">
            <Bar width="260px" />
          </span>
        </div>
      )}

      <SkeletonRows cols={cols} rows={rows} />
    </Loading>
  );
}
