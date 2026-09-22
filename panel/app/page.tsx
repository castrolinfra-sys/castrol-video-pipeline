import { Suspense } from "react";
import Link from "next/link";
import { Filters } from "./filters";
import { PageHead } from "./ui";
import { Problem } from "./problem";
import { Pager, PastEnd } from "./pager";
import { Bar, Loading, SkeletonRows } from "./skeleton";
import { db, queryDeadline } from "@/lib/db";
import { duration, num, pill, secs, ts } from "@/lib/format";
import { isPastEnd, pageBounds, pageCount, parsePage } from "@/lib/paging";
import { bounds, isRange, type RangeKey } from "@/lib/range";

export const metadata = { title: "Jobs" };
export const dynamic = "force-dynamic";

// The page itself fetches nothing, and that is the point.
//
// It used to await the whole query before returning a single byte, so a slow or
// stalled Supabase read held up the ENTIRE response — on a client-side
// navigation that shows as a URL that changed, a chip spinner that turns, and a
// page that never arrives. Now the shell and the filter chips are sent
// immediately and only the table waits.
//
// The `key` is what makes this work for a filter change. A Link that alters
// only the query string re-renders the SAME route segment, so `loading.tsx`
// never mounts; changing the key makes React treat it as a new subtree and show
// the fallback. That is the skeleton you get when you click a range.
export default async function Jobs({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; range?: string; page?: string }>;
}) {
  const params = await searchParams;
  const q = (params.q ?? "").trim();
  const range: RangeKey = isRange(params.range) ? params.range : "7d";
  const page = parsePage(params.page);

  // `page` is in the key for the same reason range and q are: without it,
  // paging is a same-segment navigation and the skeleton never shows.
  return (
    <Suspense key={`${range}:${q}:${page}`} fallback={<JobsPending q={q} range={range} />}>
      <JobsTable q={q} range={range} page={page} />
    </Suspense>
  );
}

/**
 * The waiting state.
 *
 * The REAL Filters, not a placeholder for them: the chips have to stay live
 * while the table loads, or clicking "30 days" mid-load hits a dead strip and
 * the range you just picked stops looking selected. Only the heading's count
 * and the rows are unknown, so only those are bars.
 */
function JobsPending({ q, range }: { q: string; range: RangeKey }) {
  return (
    <Loading>
      <PageHead title="Jobs" meta={<Bar width="220px" />} />
      <Filters action="/" q={q} range={range} />
      <SkeletonRows cols={8} />
    </Loading>
  );
}

// Reads job_usage (migration 0007), not `jobs` joined to anything. That view
// carries no cost, vendor, model or stage column, so nothing on this page can
// grow one by accident.
async function JobsTable({ q, range, page }: { q: string; range: RangeKey; page: number }) {
  const span = bounds(range);

  // Both identifiers, one box. The mechanic ID is what the client's own system
  // calls this person; the WhatsApp number is what they are reached on. Someone
  // chasing a specific mechanic has one or the other to hand.
  //
  // PostgREST builds `or=` from a comma- and dot-separated grammar, and SQL LIKE
  // reads % and _ as wildcards. Both sets are stripped rather than escaped: a
  // mechanic ID or a phone number contains none of them, so there is nothing to
  // lose, and a search box that can extend the filter it is interpolated into
  // is a search box that can read other columns. Stripped ONCE, here, because
  // the same string goes to the rows and to the totals.
  const term = q.replace(/[%_,.()*\\]/g, "");

  let rowsQuery = db
    .from("job_usage")
    .select(
      "job_id, status, created_at, failure_reason, mechanic_id, " +
        "whatsapp_number, user_name, workshop_name, video_seconds, video_url",
      { count: "exact" },
    );
  if (span.from) rowsQuery = rowsQuery.gte("created_at", span.from);
  if (span.to) rowsQuery = rowsQuery.lt("created_at", span.to);
  if (term) {
    rowsQuery = rowsQuery.or(`mechanic_id.ilike.%${term}%,whatsapp_number.ilike.%${term}%`);
  }

  const { from, to } = pageBounds(page);

  // Two queries, in parallel.
  //
  // The rows, with `count: "exact"` riding on them so the page count costs no
  // extra round trip. Measured on the live view: a page 4-5ms, and the deepest
  // page at ~10k rows extrapolates to ~60ms - the view's per-row subqueries
  // also run for every row an OFFSET skips, at ~5us each.
  //
  // And the heading's totals - jobs, delivered, seconds of video - exact across
  // EVERY page, from `job_usage_totals` (migration 0015). A sum over the rows
  // on screen would read as a total while being one page's worth, and
  // PostgREST aggregates are off on this project, so the sum is done in SQL.
  // That function restates the window and search above in SQL, so the two MUST
  // stay in step: it was verified against this exact query path for every range
  // and for both kinds of search when it was written. Change one, change both.
  const [rowsQ, totalsQ] = await Promise.all([
    rowsQuery
      .order("created_at", { ascending: false })
      // Tiebreaker. created_at is not unique - a batch written in one transaction
      // shares `now()` - and offset pages over a non-total order can repeat or
      // skip a row at the boundary. None share one today; nothing promises that.
      .order("job_id", { ascending: false })
      .range(from, to)
      // Without this the fetch has no deadline and a stalled read waits forever.
      .abortSignal(queryDeadline()),
    db
      .rpc("job_usage_totals", {
        p_from: span.from ?? null,
        p_to: span.to ?? null,
        p_q: term || null,
      })
      .abortSignal(queryDeadline())
      .single(),
  ]);

  const params: Record<string, string> = { range };
  if (q) params.q = q;

  if (isPastEnd(rowsQ.error)) {
    return (
      <>
        <PageHead title="Jobs" />
        <Filters action="/" q={q} range={range} />
        <PastEnd path="/" params={params} />
      </>
    );
  }
  if (rowsQ.error) return <Problem what="jobs" message={rowsQ.error.message} />;

  // PGRST202 is "no such function" - the panel deployed before migration 0015
  // was applied. That degrades to a heading without the totals rather than to
  // an error page, so the deploy order cannot take the Jobs table down with it.
  // Any other failure of the totals is a real one and says so.
  const totalsMissing = totalsQ.error?.code === "PGRST202";
  if (totalsQ.error && !totalsMissing) {
    return <Problem what="job totals" message={totalsQ.error.message} />;
  }
  const totals = totalsMissing
    ? null
    : (totalsQ.data as { jobs: number; delivered: number; seconds: number | string });

  const rows = rowsQ.data ?? [];
  const total = rowsQ.count ?? rows.length;
  const pages = pageCount(total);
  return (
    <>
      <PageHead
        title="Jobs"
        meta={
          <>
            {num(total)} {total === 1 ? "job" : "jobs"}
            {totals ? (
              <>
                <Sep />
                {num(Number(totals.delivered))} delivered
                <Sep />
                {duration(totals.seconds)} of video
              </>
            ) : null}
            {pages > 1 ? (
              <>
                <Sep />
                page {num(page)} of {num(pages)}
              </>
            ) : null}
          </>
        }
      />

      <Filters action="/" q={q} range={range} />

      {!rows.length ? (
        <p className="empty">
          {q ? `Nothing matches “${q}” in this period.` : "No jobs in this period."}
        </p>
      ) : (
        <div
          className="scroll"
          role="region"
          aria-label="Jobs"
          tabIndex={0}
        >
          <table>
            <caption className="visually-hidden">
              Jobs, most recent first. Columns: mechanic ID, WhatsApp number,
              mechanic, workshop, status, duration, created, video.
            </caption>
            <thead>
              <tr>
                <th scope="col">Mechanic ID</th>
                <th scope="col">WhatsApp</th>
                <th scope="col">Mechanic</th>
                <th scope="col">Workshop</th>
                <th scope="col">Status</th>
                <th scope="col" className="num">Duration</th>
                <th scope="col">Created (IST)</th>
                <th scope="col">Video</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((j: any) => (
                <tr key={j.job_id}>
                  <td className="mono">
                    <Link href={`/jobs/${j.job_id}`}>
                      {j.mechanic_id || "—"}
                    </Link>
                  </td>
                  <td className="mono">{j.whatsapp_number ?? "—"}</td>
                  <td>{j.user_name ?? "—"}</td>
                  <td className="dim">{j.workshop_name ?? "—"}</td>
                  <td>
                    <span className={pill(j.status)}>{j.status}</span>
                  </td>
                  <td className="num">{secs(j.video_seconds)}</td>
                  <td className="dim">{ts(j.created_at)}</td>
                  <td>
                    {/* The full CloudFront url is ~90 characters and would set
                        the width of the whole table, so it is a link rather
                        than text. It is still an anchor, so "copy link address"
                        gets the url itself, and title= shows it on hover.
                        Invariant 22: always a CDN url, never presigned - safe
                        to put on a page and safe to forward. */}
                    {j.video_url ? (
                      <a
                        href={j.video_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        title={j.video_url}
                      >
                        Watch
                        <span className="visually-hidden">
                          {" "}
                          {j.user_name ?? "this job"}’s video, opens in a new tab
                        </span>
                      </a>
                    ) : (
                      <span className="dim">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Pager path="/" params={params} page={page} total={total} label="Jobs" />
    </>
  );
}

// A separator that is punctuation to the eye and nothing to a screen reader,
// which would otherwise read "middle dot" between every figure.
function Sep() {
  return <span aria-hidden="true" style={{ opacity: 0.45, padding: "0 6px" }}>·</span>;
}
