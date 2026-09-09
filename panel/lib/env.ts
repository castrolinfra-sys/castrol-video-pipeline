// Every value the panel needs, resolved lazily and loudly.
//
// Lazily on purpose: `next build` imports these modules with no runtime
// environment, so a top-level throw here turns a missing var into a broken
// BUILD rather than a clear error on the page that needs it.
//
// Loudly on purpose too: a silently missing var renders an empty jobs table,
// which is indistinguishable from "nothing ran last night".

function required(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`${name} is not set — see panel/.env.local.example`);
  return v;
}

/** Browser-safe. Used only to establish an auth session. */
export const supabaseUrl = () => required("NEXT_PUBLIC_SUPABASE_URL");
export const publishableKey = () => required("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY");

/**
 * Emails permitted to sign in, comma-separated.
 *
 * RLS on this project is deny-all with NO policies, so a signed-in user's own
 * token reads nothing at all — every query runs with the secret key instead.
 * That makes this list the ONLY access control in the product.
 */
export const allowedEmails = (): string[] =>
  (process.env.ADMIN_ALLOWED_EMAILS ?? "")
    .split(",")
    .map((e) => e.trim().toLowerCase())
    .filter(Boolean);

export function isAllowed(email: string | null | undefined): boolean {
  return !!email && allowedEmails().includes(email.toLowerCase());
}
