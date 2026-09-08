"use server";

// The panel's ONLY write.
//
// It inserts into job_reports and nothing else. It deliberately does not touch
// `jobs`: orchestrator.py owns every write to that table, and a failed job's
// status is the pipeline's own state, not a place to record that a human
// looked at it. See supabase/migrations/0005 for the full reasoning.

import { revalidatePath } from "next/cache";
import { db } from "@/lib/db";
import { currentAdmin } from "@/lib/session";

export async function markReported(jobId: string, note: string | null) {
  const admin = await currentAdmin();
  if (!admin) throw new Error("not signed in");

  const { error } = await db
    .from("job_reports")
    .insert({ job_id: jobId, reported_by: admin, note: note || null });
  if (error) throw new Error(error.message);

  // Mirror it into the durable per-job trail, which is where anyone asking
  // about this job in six months will look. Best-effort: failing to log must
  // never undo a mark that already succeeded (invariant 25's reasoning).
  await db.from("job_events").insert({
    job_id: jobId,
    level: "info",
    event: "admin.reported",
    fields: { reported_by: admin, note: note || null },
    worker: "panel",
  });

  revalidatePath("/failures");
  revalidatePath(`/jobs/${jobId}`);
}

export async function unmarkReported(reportId: number, jobId: string) {
  const admin = await currentAdmin();
  if (!admin) throw new Error("not signed in");
  const { error } = await db.from("job_reports").delete().eq("id", reportId);
  if (error) throw new Error(error.message);
  revalidatePath("/failures");
  revalidatePath(`/jobs/${jobId}`);
}
