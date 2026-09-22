import Link from "next/link";
import { LinkPending } from "./pending";
import { num } from "@/lib/format";
import { PAGE_SIZE, pageCount } from "@/lib/paging";

/**
 * Previous / Next, and where you are.
 *
 * The links carry every other query parameter through - a date range and a
 * search survive paging - and change only `page`. Like the range chips they
 * alter the query string alone, so prefetch is off (the prefetched shell is
 * never used for a same-segment navigation) and LinkPending supplies feedback.
 *
 * Numbered, not only Previous / Next: at ~10k rows there are about 100 pages,
 * and reaching the oldest by clicking Next is a hundred clicks. The window is
 * first, last, and the current page with one either side — enough to jump to
 * either end or step, without a row of 100 links.
 *
 * Renders nothing for a result that fits on one page: a pager with both
 * buttons disabled is furniture.
 */
export function Pager({
  path,
  params,
  page,
  total,
  label,
}: {
  path: string;
  params: Record<string, string>;
  page: number;
  total: number;
  label: string;
}) {
  const pages = pageCount(total);
  if (pages <= 1) return null;

  const href = (p: number) => {
    const qs = new URLSearchParams(params);
    if (p > 1) qs.set("page", String(p));
    else qs.delete("page");
    const s = qs.toString();
    return s ? `${path}?${s}` : path;
  };

  const first = (page - 1) * PAGE_SIZE + 1;
  const last = Math.min(page * PAGE_SIZE, total);

  return (
    <nav className="pager" aria-label={`${label} pages`}>
      <span className="dim">
        {num(first)}–{num(last)} of {num(total)}
      </span>
      <span className="pager-links">
        {page > 1 ? (
          <Link className="btn" prefetch={false} href={href(page - 1)} rel="prev">
            <LinkPending>Previous</LinkPending>
          </Link>
        ) : (
          <span className="btn" aria-disabled="true">Previous</span>
        )}
        {/* The phone form. Previous + up to seven numbers + Next does not fit
            in 343px, and wrapping left Next orphaned on a line of its own; so
            under 520px the numbers give way to a plain position. */}
        <span className="pager-compact dim">
          Page {num(page)} of {num(pages)}
        </span>
        <span className="pager-pages">
          {window(page, pages).map((p, i) =>
            p === null ? (
              <span key={`gap${i}`} className="dim" aria-hidden="true">
                …
              </span>
            ) : p === page ? (
              <span key={p} className="btn current" aria-current="page">
                {num(p)}
              </span>
            ) : (
              <Link
                key={p}
                className="btn"
                prefetch={false}
                href={href(p)}
                aria-label={`Page ${p}`}
              >
                <LinkPending>{num(p)}</LinkPending>
              </Link>
            ),
          )}
        </span>
        {page < pages ? (
          <Link className="btn" prefetch={false} href={href(page + 1)} rel="next">
            <LinkPending>Next</LinkPending>
          </Link>
        ) : (
          <span className="btn" aria-disabled="true">Next</span>
        )}
      </span>
    </nav>
  );
}

/**
 * Which page numbers to draw: 1, the last, and the current one with a
 * neighbour either side. `null` is a gap. Pages 1..7 are drawn in full, because
 * an ellipsis standing in for a single hidden number costs as much room as the
 * number itself.
 */
function window(page: number, pages: number): (number | null)[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  const keep = new Set([1, pages, page - 1, page, page + 1]);
  const out: (number | null)[] = [];
  let prev = 0;
  for (let p = 1; p <= pages; p++) {
    if (!keep.has(p)) continue;
    if (p - prev > 1) out.push(null);
    out.push(p);
    prev = p;
  }
  return out;
}

/** A page number past the end - a stale link, most likely. */
export function PastEnd({ path, params }: { path: string; params: Record<string, string> }) {
  const qs = new URLSearchParams(params);
  qs.delete("page");
  const s = qs.toString();
  return (
    <p className="empty">
      There is nothing on this page — the list is shorter than it was.{" "}
      <Link href={s ? `${path}?${s}` : path}>Back to page 1</Link>
    </p>
  );
}
