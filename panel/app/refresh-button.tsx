"use client";

// Re-read the current page.
//
// Every page here is `force-dynamic` and answers "what is true right now", but
// nothing polls — so a page left open goes stale silently. This re-runs the
// server components for the current route without a full document reload,
// keeping scroll position and the filters in the URL.
//
// useTransition rather than a useState flag, and the callback is SYNCHRONOUS on
// purpose: React keeps isPending true until the router finishes the refresh, so
// the label is honest about when the data actually lands. (An async callback
// would drop isPending at the first await, which is the bug this pattern is
// usually written with.)

import { useTransition } from "react";
import { useRouter } from "next/navigation";

export function RefreshButton() {
  const router = useRouter();
  const [pending, start] = useTransition();

  return (
    <button
      onClick={() => start(() => router.refresh())}
      disabled={pending}
      // No aria-label: the visible text is the name. An aria-label that does
      // not contain the visible label breaks voice control (WCAG 2.5.3), and
      // the icon beside it is already aria-hidden.
    >
      <span className={pending ? "spin" : undefined} aria-hidden="true" style={{ display: "inline-flex", verticalAlign: "-2px" }}>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
          <path d="M21 12a9 9 0 1 1-2.64-6.36" />
          <path d="M21 3v6h-6" />
        </svg>
      </span>{" "}
      {pending ? "Refreshing…" : "Refresh"}
    </button>
  );
}
