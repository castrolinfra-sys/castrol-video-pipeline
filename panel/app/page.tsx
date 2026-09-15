import Link from "next/link";
import { Filters } from "./filters";
import { PageHead } from "./ui";
import { Problem } from "./problem";
import { db } from "@/lib/db";
import { duration, num, pill, secs, ts } from "@/lib/format";
import { bounds, isRange, type RangeKey } from "@/lib/range";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 500;

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
        "whatsapp_number, user_name, workshop_name, video_seconds, video_url",
    )
    .order("created_at", { ascending: false })
    // 500 is the cap; the 501st row is fetched only to find out whether there
    // IS one, then dropped. This replaced `{ count: "exact" }`, which makes
    // PostgREST run a real COUNT(*) over the filtered view on every page load
    // — a second scan, to print a number nobody acts on. "First 500" answers
    // the only question that matters: am I seeing everything?
    .limit(PAGE_SIZE + 1);

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

  const fetched = jobs ?? [];
  const truncated = fetched.length > PAGE_SIZE;
  const rows = truncated ? fetched.slice(0, PAGE_SIZE) : fetched;
  const delivered = rows.filter((j: any) => j.status === "completed");
  const totalSeconds = delivered.reduce(
    (n: number, j: any) => n + Number(j.video_seconds ?? 0),
    0,
  );
  return (
    <>
      <PageHead
        title="Jobs"
        meta={
          <>
            {truncated ? `First ${num(PAGE_SIZE)}` : `${num(rows.length)} shown`}
            <Sep />
            {num(delivered.length)} delivered
            <Sep />
            {duration(totalSeconds)} of video
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
    </>
  );
}

// A separator that is punctuation to the eye and nothing to a screen reader,
// which would otherwise read "middle dot" between every figure.
function Sep() {
  return <span aria-hidden="true" style={{ opacity: 0.45, padding: "0 6px" }}>·</span>;
}
