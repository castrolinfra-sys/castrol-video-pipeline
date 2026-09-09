// Auth session, kept strictly separate from the data handle in lib/db.ts.
//
// This client uses the PUBLISHABLE key and exists for one purpose: to tell us
// who is looking at the page. It reads no pipeline data - it cannot, RLS is
// deny-all and it holds no elevated role.

import { createServerClient, type CookieOptions } from "@supabase/ssr";
import { cookies } from "next/headers";
import { isAllowed, publishableKey, supabaseUrl } from "./env";

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
 * Returns null for a valid Supabase session whose email is not on the
 * allowlist - being able to authenticate is not the same as being allowed in,
 * and Supabase Auth will happily mint a session for any address that can
 * receive a magic link.
 */
export async function currentAdmin(): Promise<string | null> {
  const supabase = await sessionClient();
  const { data } = await supabase.auth.getUser();
  const email = data.user?.email ?? null;
  return isAllowed(email) ? email!.toLowerCase() : null;
}
