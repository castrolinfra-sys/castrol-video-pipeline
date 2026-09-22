// Email and password. The password itself never touches client state: the form
// posts straight to a server action, and the reveal toggle in PasswordField
// only flips the input's `type`.

import { signIn } from "./actions";
import { PasswordField } from "./password-field";

export const metadata = { title: "Sign in" };

export default async function Login({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; denied?: string; next?: string }>;
}) {
  const params = await searchParams;

  // `.screen` centres against the VIEWPORT, not against <main>. main is
  // max-width:1400px and flush left, so on a monitor wider than that the card
  // was centred inside the left 1400px and sat visibly left of centre.
  return (
    <div className="screen">
      <div className="center card stack">
        <h1 style={{ margin: 0 }}>Pipeline admin</h1>

        {params.denied ? (
          <p className="bad" style={{ margin: 0 }}>
            That account is signed in but not on the admin list. Contact the
            development team for access, or sign in as a different address.
          </p>
        ) : (
          <p className="dim" style={{ margin: 0 }}>
            Sign in with your admin email and password.
          </p>
        )}

        <form action={signIn} className="stack">
          <input type="hidden" name="next" value={params.next ?? "/"} />

          {/* Visible labels, not placeholder-as-label: a placeholder disappears
              the moment you start typing, which is exactly when you most want
              to know which field you are in. */}
          <div>
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              name="email"
              required
              autoComplete="username"
              spellCheck={false}
              autoCapitalize="none"
              placeholder="you@example.com"
            />
          </div>

          <PasswordField />

          {/* Announced, not just displayed. */}
          <div aria-live="polite">
            {params.error && (
              <p className="bad" style={{ margin: 0 }}>
                Wrong email or password.
              </p>
            )}
          </div>

          <button type="submit" className="primary" style={{ width: "100%" }}>
            Sign In
          </button>
        </form>

        <p className="dim" style={{ fontSize: "var(--t-sm)", margin: 0 }}>
          {/* Who provides accounts, not WHERE. Naming the auth provider on a
              client-facing page tells a stranger what to probe and tells the
              client nothing they can act on. */}
          Accounts are set up by the development team — there is no sign-up
          here. Contact them for access or a password reset.
        </p>
      </div>
    </div>
  );
}
