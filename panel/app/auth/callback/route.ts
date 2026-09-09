// Emailed-link landing: exchanges a code for a session cookie, then bounces to
// wherever the user was headed.
//
// Sign-in itself is email + password now, so the only thing that still arrives
// here is a PASSWORD RECOVERY link from the Supabase dashboard. Kept for that
// reason and no other - a reset that lands on a 404 is a locked-out admin.
//
// The allowlist is NOT checked here. The middleware does that on the next
// request, so there is exactly one place that decides who gets in.

import { NextResponse, type NextRequest } from "next/server";
import { createServerClient, type CookieOptions } from "@supabase/ssr";

export async function GET(request: NextRequest) {
  const { searchParams, origin } = request.nextUrl;
  const code = searchParams.get("code");
  const next = searchParams.get("next") ?? "/";

  if (!code) return NextResponse.redirect(`${origin}/login`);

  const response = NextResponse.redirect(`${origin}${next}`);
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
    {
      cookies: {
        getAll: () => request.cookies.getAll(),
        setAll: (list: { name: string; value: string; options: CookieOptions }[]) =>
          list.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options),
          ),
      },
    },
  );

  const { error } = await supabase.auth.exchangeCodeForSession(code);
  if (error) return NextResponse.redirect(`${origin}/login?error=1`);
  return response;
}
