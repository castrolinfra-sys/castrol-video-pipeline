// Auth session, kept strictly separate from the data handle in lib/db.ts.
//
// This client uses the PUBLISHABLE key and exists for one purpose: to tell us
// who is looking at the page. It reads no pipeline data - it cannot, RLS is
// deny-all and it holds no elevated role.

import { createServerClient, type CookieOptions } from "@supabase/ssr";
import { cookies, headers } from "next/headers";
import { ADMIN_EMAIL_HEADER, isAllowed, publishableKey, supabaseUrl } from "./env";

export async function sessionClient() {
  const store = await cookies();
  return createServerClient(supabaseUrl(), publishableKey(), {
    cookies: {
      getAll: () => store.getAll(),
      setAll: (list: { name: string; value: string; options: CookieOptions }[]) => {
        try {
          list.forEach(({ name, value, options }) =>
            store.set(name, value, options as Record<string, unknown>),
          );
        } catch {
          // Called from a server component, where cookies are read-only.
          // Refresh happens in middleware instead; nothing to do here.
        }
      },
    },
  });
}

/**
 * The signed-in admin's email, or null.
 *
 * Reads middleware's verdict rather than re-deriving it. This used to call
 * getUser() itself, which is a network round trip to Supabase Auth - and since
 * middleware had already made the identical call on the same request, every
 * page view paid that latency TWICE before fetching a single row.
 *
 * Middleware remains the only thing that verifies identity, exactly as it was;
 * this never was the gate, it only decides whether to draw the header. The
 * allowlist is re-checked here anyway because it is a local string compare and
 * costs nothing.
 */
export async function currentAdmin(): Promise<string | null> {
  const email = (await headers()).get(ADMIN_EMAIL_HEADER);
  return isAllowed(email) ? email!.toLowerCase() : null;
}
