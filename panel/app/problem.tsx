export function Problem({ what, message }: { what: string; message: string }) {
  const missing = /does not exist|schema cache|could not find/i.test(message);
  return (
    <div className="card">
      <h1>Could not read {what}</h1>
      <pre>{message}</pre>
      {missing && (
        <p className="dim">
          This usually means migration <code>0005</code> has not been applied.
          Run <code>uv run python scripts/apply_migration.py supabase/migrations/0005_admin_review_and_export_copy.sql</code>
        </p>
      )}
    </div>
  );
}
