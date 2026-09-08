import { db } from "@/lib/db";
import { Problem } from "../problem";
import { usd } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function Costs() {
  const { data: costs, error } = await db
    .from("job_costs")
    .select("job_id, status, cost_usd, video_cost_usd, video_seconds, paid_calls, failed_runs");
  if (error) return <Problem what="costs" message={error.message} />;

  const total = (costs ?? []).reduce((n, c: any) => n + Number(c.cost_usd ?? 0), 0);
  const done = (costs ?? []).filter((c: any) => c.status === "completed");
  const perVideo = done.length ? total / done.length : 0;

  const { data: usage } = await db
    .from("vendor_usage")
    .select("vendor, usage_date, calls, cost_usd, seconds")
    .order("usage_date", { ascending: false })
    .limit(30);
  const { data: limits } = await db
    .from("vendor_limits")
    .select("vendor, daily_call_cap, daily_cost_cap_usd, enabled, billing_unit");
  const cap = new Map((limits ?? []).map((l: any) => [l.vendor, l]));

  return (
    <>
      <h1>Costs</h1>
      <div style={{ display: "flex", gap: 16, marginBottom: 20 }}>
        <Stat label="Total spend" value={usd(total)} />
        <Stat label="Completed videos" value={String(done.length)} />
        <Stat label="Avg per completed" value={usd(perVideo)} />
      </div>

      <h2>Vendor usage vs daily cap</h2>
      <div className="scroll">
        <table>
          <thead>
            <tr><th>Date</th><th>Vendor</th><th className="num">Calls</th><th className="num">Cap</th><th className="num">Spend</th><th className="num">Cost cap</th><th className="num">Seconds</th><th>Unit</th></tr>
          </thead>
          <tbody>
            {(usage ?? []).map((u: any, i: number) => {
              const l = cap.get(u.vendor);
              const hot = l?.daily_call_cap && u.calls >= l.daily_call_cap * 0.8;
              return (
                <tr key={i} style={hot ? { color: "var(--warn)" } : undefined}>
                  <td className="mono">{u.usage_date}</td>
                  <td>{u.vendor}</td>
                  <td className="num">{u.calls}</td>
                  <td className="num dim">{l?.daily_call_cap ?? "—"}</td>
                  <td className="num">{usd(u.cost_usd)}</td>
                  <td className="num dim">{l?.daily_cost_cap_usd ? usd(l.daily_cost_cap_usd) : "—"}</td>
                  <td className="num">{u.seconds ?? "—"}</td>
                  <td className="dim">{l?.billing_unit ?? "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card" style={{ minWidth: 160 }}>
      <div className="dim" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".06em" }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>{value}</div>
    </div>
  );
}
