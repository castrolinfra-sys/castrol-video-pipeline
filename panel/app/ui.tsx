import { duration, secondsParts } from "@/lib/format";

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
 * The headline total, set rather than printed.
 *
 * "7,742s" as a string is a label. Split, with the numerals carrying the
 * weight and the unit condensed and in the brand green, it reads as a quantity
 * - and this is the one number the whole product exists to report, so it is
 * the one place worth spending the treatment.
 *
 * SECONDS, from 2026-10-28; it showed "2h 9m" before. Seconds is what the
 * vendor bills and what reconciles against an invoice line. The readable form
 * did not disappear, it moved into the tooltip - same rule as the stat cards,
 * including dropping the tooltip when the two forms would read the same.
 *
 * aria-label carries the plain form, because the visual split would otherwise
 * be announced as separated digits and letters; aria-describedby adds the
 * hr/min reading, so neither is mouse-only.
 */
export function BigDuration({ seconds }: { seconds: number }) {
  const parts = secondsParts(seconds);
  const spoken = parts.map(([v, u]) => `${v}${u}`).join(" ");
  const tip = duration(seconds);
  const showTip = tip !== spoken;
  return (
    <div
      className={showTip ? "big-dur has-tip" : "big-dur"}
      aria-label={spoken}
      tabIndex={showTip ? 0 : undefined}
      aria-describedby={showTip ? "tip-total" : undefined}
    >
      {parts.map(([value, unit]) => (
        <span key={unit} style={{ display: "contents" }}>
          <span aria-hidden="true">{value}</span>
          <span className="unit" aria-hidden="true">
            {unit}
          </span>
        </span>
      ))}
      {showTip && (
        <span className="tip" role="tooltip" id="tip-total">
          {tip}
        </span>
      )}
    </div>
  );
}
