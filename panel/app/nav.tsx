"use client";

// The primary nav.
//
// A client component for one reason: `aria-current="page"` needs the current
// path, and Next does not hand a pathname to a server layout. `usePathname` is
// already part of the shipped runtime, so the marginal cost is the component
// itself.
//
// These are `Link`, not `<a>`. As plain anchors every nav click was a full
// document reload — on pages that are all `force-dynamic` against Supabase,
// that meant a visible hang with the old page still on screen. Link prefetches
// and hands off to the route's loading skeleton instead.

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Jobs" },
  { href: "/failures", label: "Failures" },
  { href: "/submissions", label: "Submissions" },
  { href: "/usage", label: "Usage" },
] as const;

export function Nav() {
  const pathname = usePathname();

  return (
    <nav aria-label="Primary">
      {LINKS.map(({ href, label }) => {
        // A job detail page lives under /jobs/…, which belongs to Jobs.
        const active =
          href === "/"
            ? pathname === "/" || pathname.startsWith("/jobs")
            : pathname.startsWith(href);
        return (
          <Link key={href} href={href} aria-current={active ? "page" : undefined}>
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
