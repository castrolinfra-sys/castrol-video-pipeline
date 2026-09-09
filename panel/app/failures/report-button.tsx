"use client";

import { useState, useTransition } from "react";
import { markReported, unmarkReported } from "../actions";

export function ReportButton({
  jobId,
  report,
}: {
  jobId: string;
  report: { id: number } | null;
}) {
  const [pending, start] = useTransition();
  const [err, setErr] = useState<string | null>(null);

  function run(fn: () => Promise<void>) {
    setErr(null);
    start(async () => {
      try {
        await fn();
      } catch (e: any) {
        setErr(e?.message ?? "failed");
      }
    });
  }

  return (
    <>
      {report ? (
        <button disabled={pending} onClick={() => run(() => unmarkReported(report.id, jobId))}>
          {pending ? "…" : "Unmark"}
        </button>
      ) : (
        <button
          disabled={pending}
          onClick={() => {
            const note = prompt("Note (optional)") ?? null;
            run(() => markReported(jobId, note));
          }}
        >
          {pending ? "…" : "Mark reported"}
        </button>
      )}
      {err && <div style={{ color: "var(--bad)", fontSize: 11 }}>{err}</div>}
    </>
  );
}
