import { Problem } from "../problem";
import UsageChart, { type Day } from "./chart";
import { db, queryDeadline } from "@/lib/db";
import { countOf, dayLabel, duration, num, pct, secondsOnly, secs } from "@/lib/format";
import { istDay } from "@/lib/range";
import { BigDuration, PageHead } from "../ui";

export const metadata = { title: "Usage" };
export const dynamic = "force-dynamic";

// Usage, measured in seconds of video delivered.
//
// Everything here comes from daily_usage (migration 0007), which is grouped on
// the client's calendar day and carries no cost column. Period totals are sums
// over those days rather than separate queries, so every figure on the page is
// guaranteed to reconcile with the chart and the table at the bottom of it.
//
// UsageChart is imported directly rather than through next/dynamic: it is a
// client component, and Next already splits those per route, so recharts only
// ever reaches this page's bundle. next/dynamic would add indirection for a
// guarantee we already have.
export default async function Usage() {
  const { data: days, error } = await db
    .from("daily_usage")
    .select("day, jobs, completed, failed, seconds")
    .order("day", { ascending: false })
    .abortSignal(queryDeadline());
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

  // Oldest-first, and only the fields the chart plots — the client component
  // should not be handed columns it has no use for.
  const chartDays: Day[] = rows
    .slice(0, 30)
    .reverse()
    .map(({ day, seconds, completed, failed }) => ({ day, seconds, completed, failed }));

  return (
    <>
      <PageHead title="Usage" />

      <div className="headline card">
        <div className="label">Total video delivered, all time</div>
        <BigDuration seconds={all.seconds} />
        <div className="dim">
          {num(Math.ceil(all.seconds))} seconds across {countOf(all.completed, "video")}
        </div>
      </div>

      {/* Seconds, not "6h 9m" - seconds is the unit of the bill and the only
          form that reconciles against an invoice line. The readable form is
          still here, on hover and on keyboard focus, via `tip`. */}
      <div className="stats">
        <Stat label="Today" value={secondsOnly(today.seconds)} tip={duration(today.seconds)} sub={countOf(today.completed, "video")} />
        <Stat label="Yesterday" value={secondsOnly(yesterday.seconds)} tip={duration(yesterday.seconds)} sub={countOf(yesterday.completed, "video")} />
        <Stat label="Last 7 days" value={secondsOnly(week.seconds)} tip={duration(week.seconds)} sub={countOf(week.completed, "video")} />
        <Stat label="Last 30 days" value={secondsOnly(month.seconds)} tip={duration(month.seconds)} sub={countOf(month.completed, "video")} />
      </div>

      <div className="stats">
        <Stat label="Success rate" value={pct(all.completed, finished)} sub={`${num(all.completed)} of ${num(finished)} finished`} />
        <Stat label="Failure rate" value={pct(all.failed, finished)} sub={`${num(all.failed)} failed`} />
        <Stat
          label="Average length"
          value={all.completed ? secs(all.seconds / all.completed) : "—"}
          tip={all.completed ? duration(all.seconds / all.completed) : undefined}
          sub="per delivered video"
        />
        <Stat
          label="Busiest day"
          value={rows.length ? secondsOnly(Math.max(...rows.map((r) => r.seconds))) : "—"}
          tip={rows.length ? duration(Math.max(...rows.map((r) => r.seconds))) : undefined}
          sub={
            rows.length
              ? dayLabel(rows.reduce((a, b) => (b.seconds > a.seconds ? b : a)).day)
              : "—"
          }
        />
      </div>

      <UsageChart days={chartDays} />

      <h2>Daily</h2>
      {!rows.length ? (
        <p className="empty">Nothing yet.</p>
      ) : (
        <div className="scroll" role="region" aria-label="Daily usage" tabIndex={0}>
          <table>
            <caption className="visually-hidden">
              Usage by day, most recent first. Columns: day, submissions,
              delivered, failed, seconds, total.
            </caption>
            <thead>
              <tr>
                <th scope="col">Day (IST)</th>
                <th scope="col" className="num">Submissions</th>
                <th scope="col" className="num">Delivered</th>
                <th scope="col" className="num">Failed</th>
                <th scope="col" className="num">Seconds</th>
                <th scope="col" className="num">Total</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.day}>
                  <td>{dayLabel(r.day)}</td>
                  <td className="num">{num(r.jobs)}</td>
                  <td className="num">{num(r.completed)}</td>
                  {/* A red number and a black number are the same number to
                      anyone who cannot separate the two hues, so the marker is
                      a shape as well as a colour (WCAG 1.4.1). */}
                  <td className={r.failed ? "num bad" : "num"}>
                    {r.failed ? <span aria-hidden="true">● </span> : null}
                    {num(r.failed)}
                  </td>
                  <td className="num">{num(Math.ceil(r.seconds))}</td>
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

/**
 * One stat card, with an optional second reading of the same figure.
 *
 * `tip` is dropped when it would render the SAME string as the value, which is
 * every total under a minute - duration(45) and secondsOnly(45) are both "45s".
 * A tooltip that repeats the number it is attached to teaches people that the
 * dotted underline is not worth hovering, so the affordance only appears where
 * there is something behind it.
 *
 * No JavaScript: CSS shows it on :hover and on :focus-visible, and
 * aria-describedby hands it to a screen reader without either. The tabIndex is
 * what makes the keyboard path exist at all - without it the hr/min form would
 * be reachable with a mouse and no other way.
 */
function Stat({
  label,
  value,
  tip,
  sub,
}: {
  label: string;
  value: string;
  tip?: string;
  sub?: string;
}) {
  const showTip = tip && tip !== value;
  const tipId = showTip ? `tip-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}` : undefined;
  return (
    <div className="card stat">
      <div className="label">{label}</div>
      <div className="value">
        {showTip ? (
          <span className="has-tip" tabIndex={0} aria-describedby={tipId}>
            {value}
            <span className="tip" role="tooltip" id={tipId}>
              {tip}
            </span>
          </span>
        ) : (
          value
        )}
      </div>
      {sub && <div className="dim sub">{sub}</div>}
    </div>
  );
}
