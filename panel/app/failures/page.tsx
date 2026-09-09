import Link from "next/link";
import { db } from "@/lib/db";
import { Problem } from "../problem";
import { ts } from "@/lib/format";
import { failureText } from "@/lib/reasons";
import { ReportButton } from "./report-button";

export const dynamic = "force-dynamic";

// What did not get made, and for whom.
//
// No stage, no attempt count, no error code. How many times we retried before
// giving up is our business; what the client needs is which mechanic has no
// video and roughly why, in words that suggest what to do about it.
export default async function Failures() {
  const { data: jobs, error } = await db
    .from("job_usage")
    .select(
      "job_id, status, created_at, failure_reason, mechanic_id, " +
        "whatsapp_number, user_name, workshop_name",
    )
    .eq("status", "failed")
    .order("created_at", { ascending: false });
  if (error) return <Problem what="failed jobs" message={error.message} />;

  const { data: reports, error: rErr } = await db
    .from("job_reports")
    .select("id, job_id, reported_by, note, created_at")
    .order("created_at", { ascending: false });
  if (rErr) return <Problem what="job_reports" message={rErr.message} />;

  const byJob = new Map<string, any>();
  for (const r of reports ?? []) if (!byJob.has(r.job_id)) byJob.set(r.job_id, r);

  if (!jobs?.length) return <p className="empty">No failures. </p>;

  const open = jobs.filter((j: any) => !byJob.has(j.job_id));

  return (
    <>
      <h1>
        Failures{" "}
        <span className="dim">
          — {open.length} untriaged of {jobs.length}
        </span>
      </h1>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Mechanic ID</th>
              <th>WhatsApp</th>
              <th>Mechanic</th>
              <th>Workshop</th>
              <th>What happened</th>
              <th>Created</th>
              <th>Reviewed</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j: any) => {
              const r = byJob.get(j.job_id);
              return (
                <tr key={j.job_id} style={r ? { opacity: 0.55 } : undefined}>
                  <td className="mono">
                    <Link href={`/jobs/${j.job_id}`}>{j.mechanic_id || "—"}</Link>
                  </td>
                  <td className="mono">{j.whatsapp_number ?? "—"}</td>
                  <td>{j.user_name ?? "—"}</td>
                  <td className="dim">{j.workshop_name ?? "—"}</td>
                  <td style={{ whiteSpace: "normal", maxWidth: 380 }}>
                    {failureText(j.failure_reason)}
                  </td>
                  <td className="mono dim">{ts(j.created_at)}</td>
                  <td className="dim mono">
                    {r ? `${r.reported_by} · ${ts(r.created_at)}` : "—"}
                  </td>
                  <td>
                    <ReportButton jobId={j.job_id} report={r ?? null} />
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
