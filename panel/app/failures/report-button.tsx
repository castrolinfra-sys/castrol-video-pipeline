"use client";

// Marking a failure as reviewed.
//
// This used to call the native prompt(). That is unstyleable, is suppressed
// outright by some browsers, and reads to assistive tech as a browser event
// rather than as part of the page. A native <dialog> opened with showModal()
// brings focus trapping, ESC-to-close, the inert backdrop and focus-return-to-
// trigger for free — all the things a hand-built modal gets wrong.
//
// Busy state is a plain useState, NOT useTransition. This code used to be
//
//     start(async () => { await markReported(...) })
//
// and on React 18 a transition's isPending drops at the first await, not when
// the promise settles. So the button re-enabled itself while the insert was
// still in flight: the "Saving…" label flashed for a frame and a double-click
// wrote two job_reports rows. The flag below is held across the whole await.

import { useRef, useState } from "react";
import { markReported, unmarkReported } from "../actions";

export function ReportButton({
  jobId,
  mechanic,
  report,
}: {
  jobId: string;
  mechanic: string;
  report: { id: number } | null;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);

  async function run(fn: () => Promise<void>, onDone?: () => void) {
    if (busy) return;
    setErr(null);
    setBusy(true);
    try {
      await fn();
      onDone?.();
    } catch (e: any) {
      setErr(e?.message ?? "Could not save. Try again.");
    } finally {
      // The server action revalidates, so this row may already have been
      // replaced by the time we get here; setting state on an unmounted
      // component is a no-op in React 18, not a warning.
      setBusy(false);
    }
  }

  if (report) {
    return (
      <>
        <button
          disabled={busy}
          onClick={() => run(() => unmarkReported(report.id, jobId))}
        >
          {busy ? "Removing…" : "Unmark"}
        </button>
        <Err message={err} />
      </>
    );
  }

  return (
    <>
      <button onClick={() => dialog.current?.showModal()}>Mark Reported</button>

      <dialog ref={dialog} aria-labelledby={`report-title-${jobId}`}>
        <form
          method="dialog"
          onSubmit={(e) => {
            // method="dialog" would close the dialog before the action ran, so
            // the submit is taken over and the close happens on success.
            e.preventDefault();
            const note = new FormData(e.currentTarget).get("note");
            void run(
              () => markReported(jobId, typeof note === "string" ? note.trim() || null : null),
              () => dialog.current?.close(),
            );
          }}
        >
          <h2 id={`report-title-${jobId}`} style={{ margin: "0 0 4px" }}>
            Mark Reported
          </h2>
          <p className="dim" style={{ marginTop: 0 }}>
            Records that {mechanic}’s failed job has been passed on. Does not
            retry anything.
          </p>

          <label htmlFor={`note-${jobId}`}>Note (optional)</label>
          <textarea
            id={`note-${jobId}`}
            name="note"
            rows={3}
            placeholder="e.g. Asked for a new photo…"
          />

          <Err message={err} />

          <div className="row" style={{ justifyContent: "flex-end", marginTop: 16 }}>
            <button type="button" onClick={() => dialog.current?.close()} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className="primary" disabled={busy}>
              {busy ? "Saving…" : "Mark Reported"}
            </button>
          </div>
        </form>
      </dialog>
    </>
  );
}

// aria-live so the failure is announced rather than only appearing.
function Err({ message }: { message: string | null }) {
  return (
    <div aria-live="polite">
      {message && (
        <div className="bad" style={{ fontSize: 12, marginTop: 6 }}>
          {message}
        </div>
      )}
    </div>
  );
}
