import Link from "next/link";

// The catch-all 404: any URL in this app that resolves to nothing.
//
// It used to carry the JOB-specific copy ("there is no job with that
// reference"), because the only notFound() call in the app is the one in
// /jobs/[id]. That was wrong for every other address — a mistyped or stale link
// to anything at all was told, confidently, that some job did not exist. The
// job wording now lives in app/jobs/[id]/not-found.tsx, where Next routes a
// notFound() from that segment; this one says only what it actually knows.
export default function NotFound() {
  return (
    <div className="card stack">
      <h1>Page not found</h1>
      <p className="dim">
        There is nothing at this address. The link may be incomplete, or the
        page may have moved.
      </p>
      <div className="row">
        <Link className="btn" href="/">
          Back to Jobs
        </Link>
      </div>
    </div>
  );
}
