import Link from "next/link";
import { db } from "@/lib/db";
import { Problem } from "../problem";
import { ts } from "@/lib/format";
import { ReportButton } from "./report-button";

export const dynamic = "force-dynamic";

export default async function Failures() {
  const { data: jobs, error } = await db
    .from("jobs")
    .select(
      "id, status, current_stage, failure_reason, created_at, " +
        "submissions ( user_name, workshop_name, phone_e164 )",
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

  if (!jobs?.length) return <p className="empty">No failed jobs. </p>;

  const open = jobs.filter((j: any) => !byJob.has(j.id));

  return (
    <>
      <h1>
        Failures <span className="dim">— {open.length} untriaged of {jobs.length}</span>
      </h1>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Created</th><th>Mechanic</th><th>Stage</th>
              <th>Reason</th><th>Reported</th><th></th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j: any) => {
              const s = Array.isArray(j.submissions) ? j.submissions[0] : j.submissions;
              const r = byJob.get(j.id);
              return (
                <tr key={j.id} style={r ? { opacity: 0.55 } : undefined}>
                  <td className="mono"><Link href={`/jobs/${j.id}`}>{ts(j.created_at)}</Link></td>
                  <td>{s?.user_name ?? "—"}</td>
                  <td className="dim">{j.current_stage}</td>
                  <td style={{ whiteSpace: "normal", maxWidth: 420 }}>
                    {j.failure_reason ?? <span className="dim">—</span>}
                  </td>
                  <td className="dim mono">
                    {r ? `${r.reported_by} · ${ts(r.created_at)}` : "—"}
                  </td>
                  <td><ReportButton jobId={j.id} report={r ?? null} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
