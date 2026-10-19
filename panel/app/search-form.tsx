"use client";

// The search box, as a CLIENT transition rather than a document navigation.
//
// It was a bare `<form method="get">`, which is a FULL page load: the browser
// throws the current document away, asks for the url, and paints nothing new
// until the server answers - middleware's getUser() round trip to Supabase Auth
// (~60ms, see CLAUDE.md), the root layout, then the page. The old page sits
// frozen on screen the whole time with only the tab's own spinner to show for
// it, which is why searching felt far heavier than clicking a range chip.
//
// None of the work this page already does for that case applied, because all of
// it is client-side: the keyed `<Suspense>` in app/page.tsx only shows its
// skeleton for a navigation the ROUTER performs. A form GET never reaches the
// router.
//
// The query was never the slow part and this is measured, not assumed: against
// the live view, the rows query plans and executes in 1.3ms with the search
// applied, and a search costs the same as no search (72ms vs 72ms round trip
// from a desktop, almost all of it network). Measured 2026-10-19 at 297 rows.
//
// router.push puts it on the same path the range chips take, so the Suspense
// key changes and the skeleton appears at once. The native form is left intact
// underneath - method, action and the hidden range field - so with JavaScript
// off or still loading, submitting it is the old full navigation rather than a
// dead button.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTransition } from "react";
import { LinkPending } from "./pending";
import type { RangeKey } from "@/lib/range";

export function SearchForm({
  action,
  q,
  range,
}: {
  action: string;
  q: string;
  range: RangeKey;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  return (
    <form
      method="get"
      action={action}
      onSubmit={(e) => {
        e.preventDefault();
        const entered = new FormData(e.currentTarget).get("q");
        const term = typeof entered === "string" ? entered.trim() : "";

        // The range survives a search; `page` deliberately does not. A new
        // search has its own result set, and keeping page=7 would land on a
        // page past the end of it.
        const params = new URLSearchParams({ range });
        if (term) params.set("q", term);

        startTransition(() => router.push(`${action}?${params}`));
      }}
    >
      <input type="hidden" name="range" value={range} />
      <div className="field">
        <label htmlFor="q">Search</label>
        <input
          id="q"
          type="text"
          name="q"
          defaultValue={q}
          placeholder="Mechanic ID or WhatsApp number…"
          autoComplete="off"
          spellCheck={false}
        />
      </div>
      <button type="submit">
        Search
        {/* Same zero-width ring the chips use, for the gap between the click
            and the first byte of the streamed shell. */}
        <span className="link-spin" data-pending={pending || undefined} aria-hidden="true" />
      </button>
      {pending && (
        <span className="visually-hidden" role="status">
          Searching…
        </span>
      )}
      {q && (
        <Link className="btn" prefetch={false} href={`${action}?range=${range}`}>
          <LinkPending>Clear</LinkPending>
        </Link>
      )}
    </form>
  );
}
