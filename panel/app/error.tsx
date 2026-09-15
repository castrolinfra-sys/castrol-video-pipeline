"use client";

// Route-level error boundary.
//
// This is a client-facing surface, so it leads with a sentence and a way out,
// not a stack trace. The digest is Next's own opaque id for the server-side
// error — it names nothing internal, and it is the one thing that makes a
// support message actionable, so it stays, quietly.

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="card stack">
      <h1>Something went wrong</h1>
      <p className="dim">
        This page could not be loaded. Trying again usually clears it — if it
        does not, send us the reference below.
      </p>
      <div className="row">
        <button className="primary" onClick={reset}>
          Try Again
        </button>
      </div>
      {error.digest && (
        <p className="dim mono" style={{ marginBottom: 0 }}>
          Reference: {error.digest}
        </p>
      )}
    </div>
  );
}
