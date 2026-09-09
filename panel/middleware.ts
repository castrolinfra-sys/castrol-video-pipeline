// Gate every page on a signed-in, allowlisted session.
//
// Deliberately a denylist-free design: everything is private except the login
// route. A new page added later is protected by default rather than by
// remembering to add it here.

import { createServerClient, type CookieOptions } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

// Just the one. There was an /auth/callback here for emailed links - magic
// link first, then password recovery - and neither is in use: sign-in is email
// and password, and a forgotten password is reset from the Supabase dashboard.
// An unauthenticated route that mints a session from a code is not worth
// keeping for a flow nobody uses.
const PUBLIC_PATHS = ["/login"];

export async function middleware(request: NextRequest) {
  const response = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
    {
      cookies: {
        getAll: () => request.cookies.getAll(),
        setAll: (list: { name: string; value: string; options: CookieOptions }[]) => {
          list.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  // getUser(), not getSession(): getSession() trusts the cookie as written,
  // which a browser can forge. getUser() verifies against Supabase.
  const { data } = await supabase.auth.getUser();
  const email = data.user?.email?.toLowerCase() ?? null;

  const allowed = (process.env.ADMIN_ALLOWED_EMAILS ?? "")
    .split(",")
    .map((e) => e.trim().toLowerCase())
    .filter(Boolean);

  const path = request.nextUrl.pathname;
  const isPublic = PUBLIC_PATHS.some((p) => path.startsWith(p));

  if (!isPublic && (!email || !allowed.includes(email))) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", path);
    if (email) url.searchParams.set("denied", "1");
    return NextResponse.redirect(url);
  }

  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
