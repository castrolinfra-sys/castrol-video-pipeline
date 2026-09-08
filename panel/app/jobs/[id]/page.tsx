import { db } from "@/lib/db";
import { Problem } from "../../problem";
import { pill, ts, usd } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function JobDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  const { data: job, error } = await db
    .from("jobs")
    .select("*, submissions ( * )")
    .eq("id", id)
    .single();
  if (error) return <Problem what="this job" message={error.message} />;

  const s: any = Array.isArray(job.submissions) ? job.submissions[0] : job.submissions;

  const [runs, events, checks, assets, delivery] = await Promise.all([
    db.from("stage_runs").select("*").eq("job_id", id).order("created_at"),
    db.from("job_events").select("*").eq("job_id", id).order("created_at", { ascending: false }),
    db.from("checks").select("*").eq("job_id", id).order("created_at"),
    db.from("assets").select("*").eq("job_id", id).order("created_at"),
    db.from("deliveries").select("*").eq("job_id", id).maybeSingle(),
  ]);

  const video = (assets.data ?? []).find((a: any) => a.kind === "video_final" && a.cdn_url);
  const total = (runs.data ?? []).reduce((n: number, r: any) => n + Number(r.cost_usd ?? 0), 0);

  return (
    <>
      <h1>
        {s?.user_name ?? "Job"} <span className="dim">— {s?.workshop_name}</span>{" "}
        <span className={pill(job.status)}>{job.status}</span>
      </h1>
      <p className="mono dim">{job.id}</p>

      {/* Invariant 22: the delivered link is a CDN URL, never presigned. */}
      {video && (
        <>
          <h2>Final video</h2>
          <video src={video.cdn_url} controls style={{ maxWidth: 320, borderRadius: 6 }} />
          <p className="mono dim" style={{ wordBreak: "break-all" }}>{video.cdn_url}</p>
        </>
      )}

      <h2>Stage runs — {usd(total)} total across every attempt</h2>
      <Table
        head={["Stage", "Status", "Try", "Vendor", "Model", "Cost", "Secs", "Error", "Finished"]}
        rows={(runs.data ?? []).map((r: any) => [
          r.stage,
          <span className={pill(r.status)}>{r.status}</span>,
          r.attempts,
          r.vendor ?? "—",
          <span className="mono">{r.model_id ?? "—"}</span>,
          usd(r.cost_usd),
          r.billed_seconds ?? "—",
          r.error_code ? `${r.error_code}: ${r.error_message ?? ""}` : "—",
          ts(r.finished_at),
        ])}
      />

      <h2>Checks</h2>
      <Table
        head={["Check", "Passed", "Score", "Details"]}
        rows={(checks.data ?? []).map((c: any) => [
          c.check_name,
          <span className={pill(c.passed ? "succeeded" : "failed")}>{c.passed ? "pass" : "fail"}</span>,
          c.score ?? "—",
          <span className="mono">{JSON.stringify(c.details)}</span>,
        ])}
      />

      <h2>Delivery</h2>
      {delivery.data ? (
        <Table
          head={["Phone", "Attempts", "Posted", "Code", "Last error"]}
          rows={[[
            delivery.data.phone_e164,
            delivery.data.attempts,
            ts(delivery.data.posted_at),
            delivery.data.response_code ?? "—",
            delivery.data.last_error ?? "—",
          ]]}
        />
      ) : (
        <p className="empty">Not delivered.</p>
      )}

      <h2>Events</h2>
      <Table
        head={["When", "Level", "Event", "Stage", "Fields"]}
        rows={(events.data ?? []).map((e: any) => [
          <span className="mono">{ts(e.created_at)}</span>,
          <span className={pill(e.level === "error" ? "failed" : e.level === "warning" ? "running" : "pending")}>{e.level}</span>,
          e.event,
          e.stage ?? "—",
          <span className="mono">{JSON.stringify(e.fields)}</span>,
        ])}
      />

      <h2>Submission as received</h2>
      <pre>{JSON.stringify(s?.raw ?? s, null, 2)}</pre>
    </>
  );
}

function Table({ head, rows }: { head: string[]; rows: any[][] }) {
  if (!rows.length) return <p className="empty">Nothing here.</p>;
  return (
    <div className="scroll">
      <table>
        <thead><tr>{head.map((h) => <th key={h}>{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{r.map((c, j) => <td key={j}>{c as any}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
