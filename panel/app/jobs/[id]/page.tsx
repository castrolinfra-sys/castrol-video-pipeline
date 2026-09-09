import { db } from "@/lib/db";
import { Problem } from "../../problem";
import { pill, secs, ts } from "@/lib/format";
import { failureText } from "@/lib/reasons";

export const dynamic = "force-dynamic";

// One mechanic's video.
//
// What is deliberately NOT here: stage runs, retry attempts, vendors, models,
// per-attempt costs, and the internal event log. All of that is operations
// detail — it belongs in `castrol show` and `castrol events`, which is where we
// look, not in a surface the client reads. What is left is the question they
// actually have: is there a video for this person, how long is it, did it reach
// them, and if not, why not.
export default async function JobDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  const { data: job, error } = await db
    .from("job_usage")
    .select("*")
    .eq("job_id", id)
    .single();
  if (error) return <Problem what="this job" message={error.message} />;

  const [delivery, link] = await Promise.all([
    db.from("deliveries").select("phone_e164, posted_at").eq("job_id", id).maybeSingle(),
    // job_usage does not carry the raw client row - it is a wide jsonb blob and
    // most pages have no use for it - so the submission id is fetched here and
    // the blob read separately.
    db.from("jobs").select("submission_id").eq("id", id).single(),
  ]);

  const submission = link.data?.submission_id
    ? await db.from("submissions").select("raw").eq("id", link.data.submission_id).maybeSingle()
    : null;

  // From the view, not from `assets` directly. Publishing writes a new asset row
  // each time it runs, so picking one out of that table shows whichever
  // re-publish the query happened to reach first - a real file, and the wrong
  // one. job_usage.video_url resolves it against `deliveries`, which holds the
  // link the mechanic was actually given. Invariant 22: always a CDN url.
  const video: string | null = job.video_url ?? null;

  return (
    <>
      <h1>
        {job.user_name ?? "Job"} <span className="dim">— {job.workshop_name}</span>{" "}
        <span className={pill(job.status)}>{job.status}</span>
      </h1>

      <div className="scroll">
        <table>
          <tbody>
            <Row label="Mechanic ID" value={job.mechanic_id || "—"} mono />
            <Row label="WhatsApp number" value={job.whatsapp_number ?? "—"} mono />
            <Row label="Contact number on card" value={job.card_phone_e164 ?? "—"} mono />
            <Row label="Address" value={job.address_raw ?? "—"} />
            <Row label="Video length" value={secs(job.video_seconds)} />
            <Row label="Received" value={ts(job.created_at)} mono />
          </tbody>
        </table>
      </div>

      {job.status === "failed" && (
        <>
          <h2>Why it failed</h2>
          <p>{failureText(job.failure_reason)}</p>
        </>
      )}

      {video && (
        <>
          <h2>Video</h2>
          <video src={video} controls style={{ maxWidth: 320, borderRadius: 6 }} />
          <p className="mono dim" style={{ wordBreak: "break-all" }}>
            <a href={video} target="_blank" rel="noopener noreferrer">{video}</a>
          </p>
        </>
      )}

      <h2>Sent to the mechanic</h2>
      {delivery.data?.posted_at ? (
        <p>
          Sent {ts(delivery.data.posted_at)} to{" "}
          <span className="mono">{delivery.data.phone_e164}</span>.
        </p>
      ) : (
        <p className="empty">
          Not sent yet. Sending is switched off while the pipeline is being
          verified; the video above is finished and will go out when it is
          switched on.
        </p>
      )}

      <h2>Submission as received</h2>
      <pre>{JSON.stringify(submission?.data?.raw ?? {}, null, 2)}</pre>
    </>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <tr>
      <th style={{ width: 200, textAlign: "left" }}>{label}</th>
      <td className={mono ? "mono" : undefined}>{value}</td>
    </tr>
  );
}
