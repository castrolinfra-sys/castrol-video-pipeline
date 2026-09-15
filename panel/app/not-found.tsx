import Link from "next/link";

// Reached when a job id does not resolve. Previously that surfaced as a raw
// PostgREST "no rows" message inside <Problem>, which reads as a broken panel
// rather than as a link that has gone stale.
export default function NotFound() {
  return (
    <div className="card stack">
      <h1>Not found</h1>
      <p className="dim">
        There is no job with that reference. It may have been removed, or the
        link may be incomplete.
      </p>
      <div className="row">
        <Link className="btn" href="/">
          Back to Jobs
        </Link>
      </div>
    </div>
  );
}
