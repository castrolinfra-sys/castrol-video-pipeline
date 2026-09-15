"use client";

// Seconds of video delivered, by day.
//
// One series, not two. Seconds is the metric this page is about and the one the
// client is billed on; delivered and failed COUNTS are a different unit, so
// stacking them onto the same bars would encode two things in one height and
// answer neither cleanly. They live in the tooltip instead, which is where
// someone looking at a specific day is already pointing.
//
// Recharts rather than a hand-rolled SVG for ResponsiveContainer: a fixed
// viewBox either squashes or overflows at 375px, and this panel has to work on
// a phone. Colours are `var(--…)` so the chart cannot drift from the palette.
//
// The wrapper is role="img" with a summary label — the daily table directly
// beneath this is the real accessible alternative, so there is nothing gained
// by exposing forty SVG nodes to a screen reader.

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { countOf, dayLabel, duration, num } from "@/lib/format";

export type Day = {
  day: string;
  seconds: number;
  completed: number;
  failed: number;
};

function TooltipBox({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const d: Day = payload[0].payload;
  return (
    <div
      className="card"
      style={{ padding: "10px 12px", fontSize: 12, boxShadow: "var(--shadow)" }}
    >
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{dayLabel(d.day)}</div>
      <div>{duration(d.seconds)} delivered</div>
      <div className="dim">
        {countOf(d.completed, "video")}
        {d.failed > 0 ? ` · ${num(d.failed)} failed` : ""}
      </div>
    </div>
  );
}

export default function UsageChart({ days }: { days: Day[] }) {
  if (!days.length) return null;

  const total = days.reduce((n, d) => n + d.seconds, 0);
  const peak = days.reduce((a, b) => (b.seconds > a.seconds ? b : a));

  return (
    <div
      className="card chart"
      role="img"
      aria-label={`Video delivered per day over the last ${days.length} days. ${duration(
        total,
      )} in total, peaking at ${duration(peak.seconds)} on ${dayLabel(peak.day)}. The table below lists every day.`}
    >
      <div className="chart-head">
        {/* The title names the series, so a one-series chart needs no legend. */}
        <span className="label">Delivered per day</span>
        <span className="dim" style={{ fontSize: 12 }}>
          Last {days.length} days
        </span>
      </div>

      <ResponsiveContainer width="100%" height={210}>
        <BarChart data={days} margin={{ top: 4, right: 4, bottom: 0, left: -4 }}>
          {/* Recessive grid: horizontal only, and dashed so it sits behind the
              bars rather than boxing them in. */}
          <CartesianGrid vertical={false} stroke="var(--line)" strokeDasharray="2 4" />
          <XAxis
            dataKey="day"
            tickFormatter={dayLabel}
            tick={{ fill: "var(--dim)", fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: "var(--line-strong)" }}
            minTickGap={28}
            dy={2}
          />
          <YAxis
            tick={{ fill: "var(--dim)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={46}
            tickFormatter={(v: number) => (v ? duration(v) : "0")}
          />
          <Tooltip
            content={<TooltipBox />}
            cursor={{ fill: "var(--surface-2)" }}
          />
          {/* --accent-mark, not --accent. The dark ink green fails both the
              lightness band and the chroma floor as a mark colour — across a
              wide bar it stops reading as green and reads as dark slate.
              Rounded data-end anchored to the baseline; the 2px surface gap
              between adjacent bars comes from barGap. */}
          <Bar
            dataKey="seconds"
            fill="var(--accent-mark)"
            radius={[4, 4, 0, 0]}
            maxBarSize={26}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
