import { Suspense } from "react";
import Link from "next/link";
import { db, queryDeadline } from "@/lib/db";
import { Problem } from "../problem";
import { num, ts } from "@/lib/format";
import { failureText } from "@/lib/reasons";
import { isPastEnd, pageBounds, pageCount, parsePage } from "@/lib/paging";
import { ReportButton } from "./report-button";
import { Pager, PastEnd } from "../pager";
import { Bar, Loading, SkeletonRows } from "../skeleton";
import { PageHead } from "../ui";

export const metadata = { title: "Failures" };
export const dynamic = "force-dynamic";

// What did not get made, and for whom.
//
// No stage, no attempt count, no error code. How many times we retried before
// giving up is our business; what the client needs is which mechanic has no
// video and roughly why, in words that suggest what to do about it.
//
// Streams, keyed on the page, for the reasons written up in app/page.tsx: a
// `?page=` change is a same-segment navigation, so loading.tsx never mounts.
export default async function Failures({
  searchParams,
}: {
  searchParams: Promise<{ page?: string }>;
}) {
  const page = parsePage((await searchParams).page);
  return (
    <Suspense key={page} fallback={<FailuresPending />}>
      <FailuresTable page={page} />
    </Suspense>
  );
}

function FailuresPending() {
  return (
    <Loading>
      <PageHead title="Failures" meta={<Bar width="160px" />} />
      <SkeletonRows cols={8} rows={8} />
    </Loading>
  );
}

async function FailuresTable({ page }: { page: number }) {
  const { from, to } = pageBounds(page);

  // Two queries, in parallel. This page used to read EVERY failed job and
  // EVERY report with no limit at all - worse than a visible cap, because
  // Supabase silently truncates an unbounded read at its max-rows setting and
  // the page would have gone on printing a confident total over a partial list.
  const [rowsQ, openQ] = await Promise.all([
    // The page, with its reports embedded rather than fetched separately: the
    // reports read is then bounded by the page instead of growing with every
    // report ever filed, and it costs no second round trip.
    db
      .from("job_usage")
      .select(
        "job_id, status, created_at, failure_reason, mechanic_id, " +
          "whatsapp_number, user_name, workshop_name, " +
          "job_reports(id, reported_by, note, created_at)",
        { count: "exact" },
      )
      .eq("status", "failed")
      .order("created_at", { ascending: false })
      // Tiebreaker. created_at is not unique - a batch written in one transaction
      // shares `now()` - and offset pages over a non-total order can repeat or
      // skip a row at the boundary. None share one today; nothing promises that.
      .order("job_id", { ascending: false })
      .order("created_at", { referencedTable: "job_reports", ascending: false })
      .range(from, to)
      .abortSignal(queryDeadline()),
    // Untriaged across ALL failures, not just this page - the number the
    // heading exists to show. An anti-join (`job_reports=is.null` on an empty
    // embed), so it stays one exact COUNT however many pages there are.
    db
      .from("job_usage")
      .select("job_id, job_reports()", { count: "exact", head: true })
      .eq("status", "failed")
      .is("job_reports", null)
      .abortSignal(queryDeadline()),
  ]);

  if (isPastEnd(rowsQ.error)) {
    return (
      <>
        <PageHead title="Failures" />
        <PastEnd path="/failures" params={{}} />
      </>
    );
  }
  if (rowsQ.error) return <Problem what="failed jobs" message={rowsQ.error.message} />;
  if (openQ.error) return <Problem what="failed jobs" message={openQ.error.message} />;

  const jobs = rowsQ.data ?? [];
  const total = rowsQ.count ?? jobs.length;

  // The heading stays even when there is nothing to list — an empty state that
  // drops the page title reads as a page that failed to load.
  if (!total) {
    return (
      <>
        <PageHead title="Failures" />
        <p className="empty">Nothing has failed. Every job so far has produced a video.</p>
      </>
    );
  }

  const pages = pageCount(total);

  return (
    <>
      <PageHead
        title="Failures"
        meta={
          `${num(openQ.count ?? 0)} untriaged of ${num(total)}` +
          (pages > 1 ? ` · page ${num(page)} of ${num(pages)}` : "")
        }
      />
      <div className="scroll" role="region" aria-label="Failed jobs" tabIndex={0}>
        <table>
          <caption className="visually-hidden">
            Failed jobs, most recent first. Rows already reviewed are shaded.
          </caption>
          <thead>
            <tr>
              <th scope="col">Mechanic ID</th>
              <th scope="col">WhatsApp</th>
              <th scope="col">Mechanic</th>
              <th scope="col">Workshop</th>
              <th scope="col">What happened</th>
              <th scope="col">Created (IST)</th>
              <th scope="col">Reviewed</th>
              <th scope="col">
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j: any) => {
              // Newest first, by the embed's own order.
              const r = j.job_reports?.[0] ?? null;
              return (
                // Shaded, not faded. The row used to carry opacity: 0.55, which
                // pushed every bit of its text below the contrast floor — a
                // reviewed row still has to be readable.
                <tr key={j.job_id} className={r ? "reviewed" : undefined}>
                  <td className="mono">
                    <Link href={`/jobs/${j.job_id}`}>{j.mechanic_id || "—"}</Link>
                  </td>
                  <td className="mono">{j.whatsapp_number ?? "—"}</td>
                  <td>{j.user_name ?? "—"}</td>
                  <td className="dim">{j.workshop_name ?? "—"}</td>
                  <td style={{ whiteSpace: "normal", maxWidth: 380 }}>
                    {failureText(j.failure_reason)}
                  </td>
                  <td className="dim">{ts(j.created_at)}</td>
                  <td className="dim">
                    {r ? `${r.reported_by} · ${ts(r.created_at)}` : "—"}
                  </td>
                  <td>
                    <ReportButton
                      jobId={j.job_id}
                      mechanic={j.user_name ?? "this mechanic"}
                      report={r}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <Pager path="/failures" params={{}} page={page} total={total} label="Failures" />
    </>
  );
}
