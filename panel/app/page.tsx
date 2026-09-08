import Link from "next/link";
import { Problem } from "./problem";
import { db } from "@/lib/db";
import { pill, ts, usd } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function Jobs() {
  const { data: jobs, error } = await db
    .from("jobs")
    .select(
      "id, status, current_stage, failure_reason, created_at, completed_at, " +
        "submissions ( user_name, workshop_name, phone_e164, address_raw )",
    )
    .order("created_at", { ascending: false })
    .limit(200);

  if (error) return <Problem what="jobs" message={error.message} />;

  const { data: costs } = await db
    .from("job_costs")
    .select("job_id, cost_usd, video_seconds, paid_calls, failed_runs");
  const cost = new Map((costs ?? []).map((c: any) => [c.job_id, c]));

  if (!jobs?.length) return <p className="empty">No jobs yet.</p>;

  return (
    <>
      <h1>Jobs</h1>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Created</th><th>Mechanic</th><th>Workshop</th>
              <th>Status</th><th>Stage</th>
              <th className="num">Cost</th><th className="num">Secs</th>
              <th className="num">Runs failed</th><th>Completed</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j: any) => {
              const s = Array.isArray(j.submissions) ? j.submissions[0] : j.submissions;
              const c = cost.get(j.id);
              return (
                <tr key={j.id}>
                  <td className="mono"><Link href={`/jobs/${j.id}`}>{ts(j.created_at)}</Link></td>
                  <td>{s?.user_name ?? "—"}</td>
                  <td className="dim">{s?.workshop_name ?? "—"}</td>
                  <td><span className={pill(j.status)}>{j.status}</span></td>
                  <td className="dim">{j.current_stage}</td>
                  <td className="num">{usd(c?.cost_usd)}</td>
                  <td className="num">{c?.video_seconds ?? "—"}</td>
                  <td className="num">{c?.failed_runs ?? 0}</td>
                  <td className="mono dim">{ts(j.completed_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
