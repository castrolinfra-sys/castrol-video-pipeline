import { db } from "@/lib/db";
import { Problem } from "../problem";
import { ts } from "@/lib/format";
import { PageHead } from "../ui";

export const dynamic = "force-dynamic";

// Every submission, exactly as the client's own API returned it.
//
// Rendered from export_rows.raw rather than from our `submissions` columns on
// purpose: the client needs the fields the PIPELINE has no use for -
// mechanic_id, the second phone, their own status - and needs them to still be
// here if they add a column next month. Columns are discovered from the data,
// so a new field appears without a code change.
export default async function Submissions() {
  const { data: rows, error } = await db
    .from("export_rows")
    .select("id, row_index, raw, submission_id, created_at, pull_id")
    .order("id", { ascending: false })
    .limit(500);
  if (error) return <Problem what="client export rows" message={error.message} />;

  if (!rows?.length) {
    return (
      <>
        <PageHead title="Submissions" />
        <p className="empty">
          Nothing pulled yet. This fills up on the next scheduled run.
        </p>
      </>
    );
  }

  // Union of every key seen, first-seen order. A renamed or added client
  // column shows up here immediately instead of being silently dropped.
  // The Set is what keeps membership O(1) — this is 500 rows by ~18 keys, and
  // `cols.includes` made it quadratic (js-set-map-lookups).
  const cols: string[] = [];
  const seen = new Set<string>();
  for (const r of rows) {
    for (const k of Object.keys(r.raw ?? {})) {
      if (!seen.has(k)) {
        seen.add(k);
        cols.push(k);
      }
    }
  }

  return (
    <>
      <PageHead
        title="Submissions"
        meta={`${rows.length} rows, ${cols.length} columns exactly as your system returned them`}
      />
      <div className="scroll" role="region" aria-label="Client submissions" tabIndex={0}>
        <table>
          <caption className="visually-hidden">
            Rows pulled from the client export, most recent first. Column names
            are the client’s own.
          </caption>
          <thead>
            <tr>
              <th scope="col">Stored (IST)</th>
              <th scope="col">Job</th>
              {cols.map((c) => (
                <th scope="col" key={c} translate="no">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r: any) => (
              <tr key={r.id}>
                <td className="dim">{ts(r.created_at)}</td>
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
