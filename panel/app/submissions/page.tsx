import { db } from "@/lib/db";
import { Problem } from "../problem";
import { ts } from "@/lib/format";

export const dynamic = "force-dynamic";

// The client's own data, exactly as their API returned it.
//
// Rendered from export_rows.raw rather than from our `submissions` columns on
// purpose: the client admin needs the fields the PIPELINE has no use for -
// mechanic_id, the second phone, their own status - and needs them to still be
// here if the client adds a column next month. Columns are discovered from the
// data, so a new field appears without a code change.
export default async function ClientData() {
  const { data: rows, error } = await db
    .from("export_rows")
    .select("id, row_index, raw, submission_id, created_at, pull_id")
    .order("id", { ascending: false })
    .limit(500);
  if (error) return <Problem what="client export rows" message={error.message} />;

  if (!rows?.length) {
    return (
      <>
        <h1>Client data</h1>
        <p className="empty">
          No export rows stored yet. Intake has not been reworked for the CSV
          schema — until it is, this fills up only once a pull runs.
        </p>
      </>
    );
  }

  // Union of every key seen, first-seen order. A renamed or added client
  // column shows up here immediately instead of being silently dropped.
  const cols: string[] = [];
  for (const r of rows) for (const k of Object.keys(r.raw ?? {})) if (!cols.includes(k)) cols.push(k);

  return (
    <>
      <h1>
        Client data <span className="dim">— {rows.length} rows, {cols.length} columns as returned</span>
      </h1>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Stored</th>
              <th>Job</th>
              {cols.map((c) => <th key={c}>{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r: any) => (
              <tr key={r.id}>
                <td className="mono dim">{ts(r.created_at)}</td>
                <td>
                  {r.submission_id
                    ? <span className="pill succeeded">accepted</span>
                    : <span className="pill pending">no job</span>}
                </td>
                {cols.map((c) => {
                  const v = r.raw?.[c];
                  const long = typeof v === "string" && v.length > 60;
                  return (
                    <td key={c} title={long ? v : undefined} className={long ? "mono dim" : undefined}>
                      {v === undefined || v === null || v === ""
                        ? <span className="dim">—</span>
                        : long ? v.slice(0, 40) + "…" : String(v)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
