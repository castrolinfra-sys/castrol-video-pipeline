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

  // `.screen` centres against the VIEWPORT, not against <main>. It was added
  // when main was capped at 1400px and flush left, so on a wider monitor the
  // card sat visibly left of centre; main is full width now, but this still
  // has to be fixed positioning because it must also centre VERTICALLY, which
  // main cannot do.
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
