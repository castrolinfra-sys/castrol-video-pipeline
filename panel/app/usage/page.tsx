import { Problem } from "../problem";
import { db } from "@/lib/db";
import { duration, num, pct } from "@/lib/format";
import { istDay } from "@/lib/range";

export const dynamic = "force-dynamic";

// Usage, measured in seconds of video delivered.
//
// Everything here comes from daily_usage (migration 0007), which is grouped on
// the client's calendar day and carries no cost column. Period totals are sums
// over those days rather than separate queries, so every figure on the page is
// guaranteed to reconcile with the table at the bottom of it.
export default async function Usage() {
  const { data: days, error } = await db
    .from("daily_usage")
    .select("day, jobs, completed, failed, seconds")
    .order("day", { ascending: false });
  if (error) return <Problem what="usage" message={error.message} />;

  const rows = (days ?? []).map((d: any) => ({
    day: String(d.day),
    jobs: Number(d.jobs ?? 0),
    completed: Number(d.completed ?? 0),
    failed: Number(d.failed ?? 0),
    seconds: Number(d.seconds ?? 0),
  }));

  const sum = (from?: string, to?: string) =>
    rows
      .filter((r) => (!from || r.day >= from) && (!to || r.day < to))
      .reduce(
        (a, r) => ({
          seconds: a.seconds + r.seconds,
          completed: a.completed + r.completed,
          failed: a.failed + r.failed,
        }),
        { seconds: 0, completed: 0, failed: 0 },
      );

  const all = sum();
  const today = sum(istDay(0));
  const yesterday = sum(istDay(1), istDay(0));
  const week = sum(istDay(6));
  const month = sum(istDay(29));

  // Rate over FINISHED work only. Counting jobs still in the queue as failures
  // makes the number swing with whatever happens to be mid-render, which is
  // noise, not information.
  const finished = all.completed + all.failed;

  return (
    <>
      <h1>Usage</h1>

      <div className="headline card">
        <div className="label">Total video delivered, all time</div>
        <div className="big">{duration(all.seconds)}</div>
        <div className="dim">
          {num(Math.round(all.seconds))} seconds across {num(all.completed)}{" "}
          videos
        </div>
      </div>

      <div className="stats">
        <Stat label="Today" value={duration(today.seconds)} sub={`${num(today.completed)} videos`} />
        <Stat label="Yesterday" value={duration(yesterday.seconds)} sub={`${num(yesterday.completed)} videos`} />
        <Stat label="Last 7 days" value={duration(week.seconds)} sub={`${num(week.completed)} videos`} />
        <Stat label="Last 30 days" value={duration(month.seconds)} sub={`${num(month.completed)} videos`} />
      </div>

      <div className="stats">
        <Stat label="Success rate" value={pct(all.completed, finished)} sub={`${num(all.completed)} of ${num(finished)} finished`} />
        <Stat label="Failure rate" value={pct(all.failed, finished)} sub={`${num(all.failed)} failed`} />
        <Stat
          label="Average length"
          value={all.completed ? `${(all.seconds / all.completed).toFixed(1)}s` : "—"}
          sub="per delivered video"
        />
        <Stat
          label="Busiest day"
          value={rows.length ? duration(Math.max(...rows.map((r) => r.seconds))) : "—"}
          sub={
            rows.length
              ? rows.reduce((a, b) => (b.seconds > a.seconds ? b : a)).day
              : "—"
          }
        />
      </div>

      <h2>Daily</h2>
      {!rows.length ? (
        <p className="empty">Nothing yet.</p>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Day</th>
                <th className="num">Submissions</th>
                <th className="num">Delivered</th>
                <th className="num">Failed</th>
                <th className="num">Seconds</th>
                <th className="num">Total</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.day}>
                  <td className="mono">{r.day}</td>
                  <td className="num">{num(r.jobs)}</td>
                  <td className="num">{num(r.completed)}</td>
                  <td className="num" style={r.failed ? { color: "var(--bad)" } : undefined}>
                    {num(r.failed)}
                  </td>
                  <td className="num">{r.seconds.toFixed(1)}</td>
                  <td className="num dim">{duration(r.seconds)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="card stat">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub && <div className="dim sub">{sub}</div>}
    </div>
  );
}
