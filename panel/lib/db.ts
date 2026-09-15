// The panel's only database handle.
//
// This client authenticates with the SECRET key, which authorizes as the
// `service_role` Postgres role and therefore BYPASSES the deny-all RLS on
// every table. That is deliberate and is the whole design: the pipeline's
// tables have no policies, so a user's own token would read zero rows.
//
// The consequence is that this module is a skeleton key, and it must never be
// bundled into anything the browser receives. The guard below turns that from
// a code-review convention into a crash.

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

// Loosely typed for now. Once migration 0005 is applied, replace this with
// generated types (`supabase gen types typescript`) so every column name in
// this app is checked against the real schema.
type Db = SupabaseClient<any, "public", any>;

if (typeof window !== "undefined") {
  throw new Error(
    "panel/lib/db.ts was imported into a client bundle. It holds the Supabase " +
      "secret key. Move the query into a server component or a server action.",
  );
}

let _db: Db | null = null;

export function getDb(): Db {
  if (_db) return _db;

  const key = process.env.SUPABASE_SECRET_KEY;
  if (!key) throw new Error("SUPABASE_SECRET_KEY is not set");

  // New-style keys only. `sb_secret_...` / `sb_publishable_...` — the legacy
  // anon/service_role JWTs were never issued to this project and anything
  // asking for one is wrong. See CLAUDE.md, "Conventions".
  if (!key.startsWith("sb_secret_")) {
    throw new Error(
      "SUPABASE_SECRET_KEY must be a new-style `sb_secret_...` key, not a " +
        "legacy service_role JWT.",
    );
  }

  _db = createClient<any>(process.env.NEXT_PUBLIC_SUPABASE_URL!, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  return _db;
}

/**
 * Every query gets a deadline.
 *
 * supabase-js puts no timeout on its fetch, so a stalled connection does not
 * fail — it waits forever, and so does the page render sitting on top of it.
 * On a client-side navigation that is indistinguishable from a broken app: the
 * URL changes, the spinner turns, and nothing ever arrives.
 *
 * Twelve seconds is well past a healthy read (measured: 100-300ms for every
 * range on this page) and well short of a person's patience. Past it the query
 * returns an error, which the pages already render through <Problem> — a
 * sentence and a Retry beats an indefinite wait.
 */
export const QUERY_TIMEOUT_MS = 12_000;

/** `AbortSignal.timeout(QUERY_TIMEOUT_MS)`, for `.abortSignal(...)`. */
export function queryDeadline(): AbortSignal {
  return AbortSignal.timeout(QUERY_TIMEOUT_MS);
}

/** Proxy so callers keep writing `db.from(...)` without eager construction. */
export const db = new Proxy({} as Db, {
  get: (_t, prop) => (getDb() as any)[prop],
});
