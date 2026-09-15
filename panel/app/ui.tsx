import { durationParts } from "@/lib/format";

// Shared page furniture. Server components, no client JS.

/**
 * The page title and the count that qualifies it, on one baseline.
 *
 * Every page had its own arrangement of an <h1> with a dim span inside it. One
 * component means the four pages line up with each other, and the summary line
 * stops being part of the heading text a screen reader reads as the title.
 */
export function PageHead({ title, meta }: { title: string; meta?: React.ReactNode }) {
  return (
    <div className="page-head">
      <h1>{title}</h1>
      {meta ? <div className="meta">{meta}</div> : null}
    </div>
  );
}

/**
 * The headline duration, set rather than printed.
 *
 * "4h 12m" as a string is a label. Split, with the numerals carrying the weight
 * and the units condensed and in the brand green, it reads as a quantity — and
 * this is the one number the whole product exists to report, so it is the one
 * place worth spending the treatment.
 *
 * aria-label carries the plain form, because the visual split would otherwise
 * be announced as separated digits and letters.
 */
export function BigDuration({ seconds }: { seconds: number }) {
  const parts = durationParts(seconds);
  const spoken = parts.map(([v, u]) => `${v}${u}`).join(" ");
  return (
    <div className="big-dur" aria-label={spoken}>
      {parts.map(([value, unit]) => (
        <span key={unit} style={{ display: "contents" }}>
          <span aria-hidden="true">{value}</span>
          <span className="unit" aria-hidden="true">
            {unit}
          </span>
        </span>
      ))}
    </div>
  );
}
