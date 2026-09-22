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

      {/* A missing table or function is ours to fix, not the reader's. This
          used to print the developer command that applies a migration - a file
          path and a CLI invocation on a client-facing page, and advice the
          person reading it could never act on. */}
      {missing && (
        <p className="dim" style={{ margin: 0 }}>
          If it keeps happening, contact the development team and include the
          technical detail below.
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
