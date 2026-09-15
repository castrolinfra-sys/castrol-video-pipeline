// A query that did not return.
//
// This is a client-facing page, so it leads with a sentence rather than a
// PostgREST error string — that string can name tables and columns, and it is
// the kind of internal detail this panel exists to keep off screen. It is still
// reachable one click down, because support cannot diagnose without it.

export function Problem({ what, message }: { what: string; message: string }) {
  const missing = /does not exist|schema cache|could not find/i.test(message);
  return (
    <div className="card stack">
      <h1 style={{ margin: 0 }}>Could not load {what}</h1>
      <p className="dim" style={{ margin: 0 }}>
        The data could not be read just now. Reloading usually clears it.
      </p>

      {missing && (
        <p className="dim" style={{ margin: 0 }}>
          This usually means migration <code>0005</code> has not been applied.
          Run{" "}
          <code>
            uv run python scripts/apply_migration.py
            supabase/migrations/0005_admin_review_and_export_copy.sql
          </code>
        </p>
      )}

      <details>
        <summary className="dim" style={{ cursor: "pointer", fontSize: "var(--t-sm)" }}>
          Technical detail
        </summary>
        <pre style={{ marginTop: "var(--s2)" }}>{message}</pre>
      </details>
    </div>
  );
}
