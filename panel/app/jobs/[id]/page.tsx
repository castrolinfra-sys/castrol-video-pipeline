import { notFound } from "next/navigation";
import { db } from "@/lib/db";
import { Problem } from "../../problem";
import { pill, secs, ts } from "@/lib/format";
import { failureText } from "@/lib/reasons";
import { PageHead } from "../../ui";

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

  // All three are independent, so they go together. Previously `job` was
  // awaited on its own first, which made this page three sequential Supabase
  // round trips instead of two (async-parallel).
  const [job, delivery, link] = await Promise.all([
    db.from("job_usage").select("*").eq("job_id", id).maybeSingle(),
    db.from("deliveries").select("phone_e164, posted_at").eq("job_id", id).maybeSingle(),
    // job_usage does not carry the raw client row - it is a wide jsonb blob and
    // most pages have no use for it - so the submission id is fetched here and
    // the blob read separately.
    db.from("jobs").select("submission_id").eq("id", id).maybeSingle(),
  ]);

  if (job.error) return <Problem what="this job" message={job.error.message} />;
  // A stale or mistyped link is a 404, not a database error. It used to surface
  // as a raw PostgREST "no rows" message, which reads as a broken panel.
  if (!job.data) notFound();

  const j = job.data;

  const submission = link.data?.submission_id
    ? await db.from("submissions").select("raw").eq("id", link.data.submission_id).maybeSingle()
    : null;

  // From the view, not from `assets` directly. Publishing writes a new asset row
  // each time it runs, so picking one out of that table shows whichever
  // re-publish the query happened to reach first - a real file, and the wrong
  // one. job_usage.video_url resolves it against `deliveries`, which holds the
  // link the mechanic was actually given. Invariant 22: always a CDN url.
  const video: string | null = j.video_url ?? null;

  return (
    <>
      <PageHead
        title={j.user_name ?? "Job"}
        meta={
          <>
            {j.workshop_name}{" "}
            <span className={pill(j.status)} style={{ marginLeft: 6 }}>
              {j.status}
            </span>
          </>
        }
      />

      <div className="scroll" role="region" aria-label="Job details" tabIndex={0}>
        <table>
          <caption className="visually-hidden">Details for this job.</caption>
          <tbody>
            <Row label="Mechanic ID" value={j.mechanic_id || "—"} mono />
            <Row label="WhatsApp number" value={j.whatsapp_number ?? "—"} mono />
            <Row label="Contact number on card" value={j.card_phone_e164 ?? "—"} mono />
            <Row label="Address" value={j.address_raw ?? "—"} />
            <Row label="Video length" value={secs(j.video_seconds)} />
            <Row label="Received (IST)" value={ts(j.created_at)} />
          </tbody>
        </table>
      </div>

      {j.status === "failed" && (
        <>
          <h2>Why it failed</h2>
          <p>{failureText(j.failure_reason)}</p>
        </>
      )}

      {video && (
        <>
          <h2>Video</h2>
          {/* Explicit dimensions so the page does not jump when the metadata
              lands. 9:16, matching what the pipeline renders. */}
          <video
            src={video}
            controls
            preload="metadata"
            width={288}
            height={512}
            aria-label={`Finished video for ${j.user_name ?? "this mechanic"}`}
            style={{ width: 288, maxWidth: "100%", height: "auto", aspectRatio: "9 / 16", borderRadius: "var(--r)", background: "#000" }}
          />
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
      <th scope="row" style={{ width: 200 }}>
        {label}
      </th>
      <td className={mono ? "mono" : undefined} style={{ whiteSpace: "normal" }}>
        {value}
      </td>
    </tr>
  );
}
