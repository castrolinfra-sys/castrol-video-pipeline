// Gate every page on a signed-in, allowlisted session.
//
// Deliberately a denylist-free design: everything is private except the login
// route. A new page added later is protected by default rather than by
// remembering to add it here.
//
// This is also the ONLY place that verifies identity, and it now publishes its
// verdict to the render via a request header. Previously the root layout called
// getUser() a second time to find out who was signed in — and getUser() is a
// network round trip to Supabase Auth, not a cookie read, so every single page
// view paid that latency twice before a single row of data was fetched.
//
// The header is not forgeable: any inbound copy is deleted below, the value is
// set only from the verified getUser() result, and the matcher runs this on
// every non-static path, so no render can be reached without passing here.

import { createServerClient, type CookieOptions } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { ADMIN_EMAIL_HEADER, allowedEmails } from "@/lib/env";

// Just the one. There was an /auth/callback here for emailed links - magic
// link first, then password recovery - and neither is in use: sign-in is email
// and password, and a forgotten password is reset from the Supabase dashboard.
// An unauthenticated route that mints a session from a code is not worth
// keeping for a flow nobody uses.
const PUBLIC_PATHS = ["/login"];

export async function middleware(request: NextRequest) {
  // Collected rather than written straight onto a response: the final response
  // cannot be built until the auth result is known, because the request headers
  // it forwards depend on it.
  const refreshed: { name: string; value: string; options: CookieOptions }[] = [];

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
    {
      cookies: {
        getAll: () => request.cookies.getAll(),
        setAll: (list: { name: string; value: string; options: CookieOptions }[]) =>
          refreshed.push(...list),
      },
    },
  );

  // getUser(), not getSession(): getSession() trusts the cookie as written,
  // which a browser can forge. getUser() verifies against Supabase.
  const { data } = await supabase.auth.getUser();
  const email = data.user?.email?.toLowerCase() ?? null;

  const allowed = allowedEmails();
  const admin = email && allowed.includes(email) ? email : null;

  const path = request.nextUrl.pathname;
  const isPublic = PUBLIC_PATHS.some((p) => path.startsWith(p));

  if (!isPublic && !admin) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", path);
    if (email) url.searchParams.set("denied", "1");
    const redirect = NextResponse.redirect(url);
    for (const c of refreshed) redirect.cookies.set(c.name, c.value, c.options);
    return redirect;
  }

  const headers = new Headers(request.headers);
  // Unconditional delete first: a client that sends this header itself must
  // never have it survive into the render, including on a public path where
  // nothing overwrites it.
  headers.delete(ADMIN_EMAIL_HEADER);
  if (admin) headers.set(ADMIN_EMAIL_HEADER, admin);

  const response = NextResponse.next({ request: { headers } });
  for (const c of refreshed) response.cookies.set(c.name, c.value, c.options);
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
