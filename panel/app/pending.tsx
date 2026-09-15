"use client";

// The pending state for a navigation that changes only the query string.
//
// This exists because of a measured gap, not a hunch. A `Link` that changes
// only searchParams re-renders the SAME route segment, and the router keeps the
// old UI on screen while it waits for the new payload — `loading.tsx` never
// mounts. Measured on a probe route: click to render 273ms, and the skeleton
// did not appear once.
//
// So for the range chips there was no feedback of any kind. As plain <a> tags
// the browser drew its own spinner in the tab and the page visibly navigated;
// converting them to Link took that away and replaced it with nothing, which is
// why clicking "Yesterday" felt like clicking a dead control.
//
// useLinkStatus reads the pending state of the enclosing Link, so this has to
// be rendered INSIDE one.

import { useLinkStatus } from "next/link";

export function LinkPending({ children }: { children: React.ReactNode }) {
  const { pending } = useLinkStatus();
  return (
    <>
      {children}
      {/* Occupies no space until it is needed, so the chip does not resize and
          shove its neighbours along when a filter is clicked. */}
      <span className="link-spin" data-pending={pending || undefined} aria-hidden="true" />
      {/* The visible chip text does not change, so the announcement is here
          rather than in the label - a control whose name changes under a
          screen reader mid-activation is worse than one that stays put. */}
      {pending && (
        <span className="visually-hidden" role="status">
          Loading…
        </span>
      )}
    </>
  );
}
