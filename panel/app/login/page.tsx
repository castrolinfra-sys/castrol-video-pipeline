// Email and password. No client JavaScript at all: the form posts straight to
// a server action, so the password never lands in component state and the page
// works before React has hydrated.

import { signIn } from "./actions";

export default async function Login({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; denied?: string; next?: string }>;
}) {
  const params = await searchParams;

  return (
    <div className="center card">
      <h1>Pipeline admin</h1>

      {params.denied ? (
        <p style={{ color: "var(--bad)", marginTop: 0 }}>
          That account is signed in but not on the admin list. Ask for access,
          or sign in as a different address.
        </p>
      ) : (
        <p className="dim" style={{ marginTop: 0 }}>
          Sign in with your admin email and password.
        </p>
      )}

      <form action={signIn}>
        <input type="hidden" name="next" value={params.next ?? "/"} />
        <input
          type="email"
          name="email"
          required
          autoComplete="username"
          placeholder="you@example.com"
        />
        <input
          type="password"
          name="password"
          required
          autoComplete="current-password"
          placeholder="Password"
          style={{ marginTop: 8 }}
        />
        <button style={{ marginTop: 12 }}>Sign in</button>

        {params.error && (
          <p style={{ color: "var(--bad)" }}>Wrong email or password.</p>
        )}
      </form>

      <p className="dim" style={{ fontSize: 12, marginBottom: 0 }}>
        Accounts are created in the Supabase dashboard, not here. There is no
        sign-up.
      </p>
    </div>
  );
}
