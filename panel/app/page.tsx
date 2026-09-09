import Link from "next/link";
import { Filters } from "./filters";
import { Problem } from "./problem";
import { db } from "@/lib/db";
import { duration, num, pill, secs, ts } from "@/lib/format";
import { bounds, isRange, type RangeKey } from "@/lib/range";

export const dynamic = "force-dynamic";

// Reads job_usage (migration 0007), not `jobs` joined to anything. That view
// carries no cost, vendor, model or stage column, so nothing on this page can
// grow one by accident.
export default async function Jobs({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; range?: string }>;
}) {
  const params = await searchParams;
  const q = (params.q ?? "").trim();
  const range: RangeKey = isRange(params.range) ? params.range : "7d";
  const { from, to } = bounds(range);

  let query = db
    .from("job_usage")
    .select(
      "job_id, status, created_at, failure_reason, mechanic_id, " +
        "whatsapp_number, user_name, workshop_name, video_seconds",
    )
    .order("created_at", { ascending: false })
    .limit(500);

  if (from) query = query.gte("created_at", from);
  if (to) query = query.lt("created_at", to);

  // Both identifiers, one box. The mechanic ID is what the client's own system
  // calls this person; the WhatsApp number is what they are reached on. Someone
  // chasing a specific mechanic has one or the other to hand, not both.
  if (q) {
    // PostgREST builds `or=` from a comma- and dot-separated grammar, and SQL
    // LIKE reads % and _ as wildcards. Both sets are stripped rather than
    // escaped: a mechanic ID or a phone number contains none of them, so there
    // is nothing to lose, and a search box that can extend the filter it is
    // interpolated into is a search box that can read other columns.
    const like = `%${q.replace(/[%_,.()*\\]/g, "")}%`;
    query = query.or(`mechanic_id.ilike.${like},whatsapp_number.ilike.${like}`);
  }

  const { data: jobs, error } = await query;
  if (error) return <Problem what="jobs" message={error.message} />;

  const rows = jobs ?? [];
  const delivered = rows.filter((j: any) => j.status === "completed");
  const totalSeconds = delivered.reduce(
    (n: number, j: any) => n + Number(j.video_seconds ?? 0),
    0,
  );

  return (
    <>
      <h1>
        Jobs{" "}
        <span className="dim">
          — {num(rows.length)} shown · {num(delivered.length)} delivered ·{" "}
          {duration(totalSeconds)} of video
        </span>
      </h1>

      <Filters action="/" q={q} range={range} />

      {!rows.length ? (
        <p className="empty">
          {q ? `Nothing matches “${q}” in this period.` : "No jobs in this period."}
        </p>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Mechanic ID</th>
                <th>WhatsApp</th>
                <th>Mechanic</th>
                <th>Workshop</th>
                <th>Status</th>
                <th className="num">Duration</th>
                <th>Created</th>
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
                  <td className="mono dim">{ts(j.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
