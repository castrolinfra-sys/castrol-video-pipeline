import Link from "next/link";
import { db, queryDeadline } from "@/lib/db";
import { Problem } from "../problem";
import { ts } from "@/lib/format";
import { failureText } from "@/lib/reasons";
import { ReportButton } from "./report-button";
import { PageHead } from "../ui";

export const metadata = { title: "Failures" };
export const dynamic = "force-dynamic";

// What did not get made, and for whom.
//
// No stage, no attempt count, no error code. How many times we retried before
// giving up is our business; what the client needs is which mechanic has no
// video and roughly why, in words that suggest what to do about it.
export default async function Failures() {
  // Independent queries, so they go together rather than one after the other
  // (async-parallel). Sequentially this page paid two Supabase round trips
  // before it could render anything.
  const [{ data: jobs, error }, { data: reports, error: rErr }] = await Promise.all([
    db
      .from("job_usage")
      .select(
        "job_id, status, created_at, failure_reason, mechanic_id, " +
          "whatsapp_number, user_name, workshop_name",
      )
      .eq("status", "failed")
      .order("created_at", { ascending: false })
      .abortSignal(queryDeadline()),
    db
      .from("job_reports")
      .select("id, job_id, reported_by, note, created_at")
      .order("created_at", { ascending: false })
      .abortSignal(queryDeadline()),
  ]);

  if (error) return <Problem what="failed jobs" message={error.message} />;
  if (rErr) return <Problem what="job_reports" message={rErr.message} />;

  const byJob = new Map<string, any>();
  for (const r of reports ?? []) if (!byJob.has(r.job_id)) byJob.set(r.job_id, r);

  // The heading stays even when there is nothing to list — an empty state that
  // drops the page title reads as a page that failed to load.
  if (!jobs?.length) {
    return (
      <>
        <PageHead title="Failures" />
        <p className="empty">Nothing has failed. Every job so far has produced a video.</p>
      </>
    );
  }

  const open = jobs.filter((j: any) => !byJob.has(j.job_id));

  return (
    <>
      <PageHead
        title="Failures"
        meta={`${open.length} untriaged of ${jobs.length}`}
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
              const r = byJob.get(j.job_id);
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
                      report={r ?? null}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
