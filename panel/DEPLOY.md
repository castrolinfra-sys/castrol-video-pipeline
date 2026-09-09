# Deploying the panel to Vercel

Ten minutes, six steps. The one that catches people is step 0.

---

## 0. Get the panel onto the branch Vercel will build

**`main` has no `panel/` directory.** Everything — the panel, the cycle, the
deploy units, migrations 0005–0008 — is on `admin-panel`, twelve commits ahead.

Vercel builds Production from the repository's **default branch**, which is
`main`. Point it at this repo today and the build fails before it starts, with
a message about the Root Directory not existing — which reads like a
misconfiguration rather than what it is.

Merge first. It is where this work belongs anyway; the EC2 deploy clones `main`
too.

```bash
git checkout main && git merge --no-ff admin-panel && git push
```

Then come back to `admin-panel`, or delete it.

*(The alternative — Vercel → Settings → Git → Production Branch → `admin-panel`
— works, but leaves the whole project living on a feature branch.)*

---

## 1. Import the project

Vercel → **Add New** → **Project** → import `castrol-video-pipeline`.

Authorise the Vercel GitHub app against the **`castrolinfra-sys`** account, not
a personal one. This project keeps a dedicated set of logins on purpose.

---

## 2. Set the Root Directory to `panel`

**Root Directory → Edit → `panel`.**

This is the only build setting that needs touching. The repo root is a Python
project; `panel/` is the Next.js app. Leave *Include files outside the root
directory* **off** — the panel imports nothing from the parent.

Framework preset auto-detects as Next.js. Do not override the build command,
output directory, or install command.

---

## 3. Environment variables

Four, and they must be set for **Production, Preview and Development**. Copy
the values out of your local `panel/.env.local`.

| Name | Value | Visible to the browser? |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | `https://<ref>.supabase.co` | yes |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | `sb_publishable_…` | yes |
| `SUPABASE_SECRET_KEY` | `sb_secret_…` | **no — never** |
| `ADMIN_ALLOWED_EMAILS` | comma-separated | no |

Three things about that table.

`NEXT_PUBLIC_` is not decoration — Next.js **inlines those values into the
JavaScript the browser downloads**. That is correct for the publishable key,
which reads nothing (RLS is deny-all with no policies). It would be a total
compromise for the secret key, which authorizes as `service_role` and bypasses
RLS on every table. **Never prefix `SUPABASE_SECRET_KEY`.** `lib/db.ts` throws
if it is ever imported into a client bundle, so the mistake crashes rather than
ships — but do not rely on that.

New-style keys only: `sb_secret_…` / `sb_publishable_…`. `lib/db.ts` rejects a
legacy `service_role` JWT by prefix. Supabase → Settings → API Keys.

`ADMIN_ALLOWED_EMAILS` **is the access control.** Not a convenience, not a
second layer — the only one. Every query runs server-side with the secret key,
so a signed-in stranger would read everything if their address were on this
list. Check it twice.

---

## 4. Deploy

Hit Deploy. The build needs no database connection — `lib/env.ts` resolves
every variable lazily precisely so a missing one is a clear error on the page
that needs it, rather than a broken build with a stack trace in it. Every page
is `force-dynamic`, so nothing is prerendered against live data.

Note the assigned URL, e.g. `https://castrol-panel.vercel.app`.

---

## 5. Tell Supabase about the URL

Supabase → **Authentication → URL Configuration**:

- **Site URL** → your Vercel production URL
- **Redirect URLs** → add `https://<your-url>/auth/callback`

Sign-in is email and password and does not need this. **Password recovery
does** — a reset link that lands anywhere else is a locked-out admin, and it is
the kind of thing you discover on the day you need it.

Add `http://localhost:3100/auth/callback` too, so recovery works locally.

---

## 6. Create the admin account, and close the door behind it

Supabase → **Authentication → Users → Add user**: the address from
`ADMIN_ALLOWED_EMAILS`, a password, **Auto Confirm User** on.

Then Supabase → **Authentication → Sign In / Providers → Email** → turn
**"Allow new users to sign up" OFF**.

With password auth and sign-ups open, anyone who finds the URL can mint an
account. The allowlist still stops them reading anything — but that is the
second line of defence, and you do not want to be standing on it.

---

## Verify

1. Open the URL in a private window → the sign-in form.
2. Wrong password → *"Wrong email or password."*
3. Correct password → the Jobs table.
4. Sign in as any address **not** on the allowlist → bounced back with
   *"That account is signed in but not on the admin list."* If that address
   gets in, `ADMIN_ALLOWED_EMAILS` did not reach the Production environment.
5. View source, or open the JS bundle in devtools, and search for
   `sb_secret_`. **It must not be there.**

---

## After it is live

Every push to `main` redeploys Production; every PR gets a preview URL.

Preview deployments are covered by Vercel's own Deployment Protection, and
production is not — production is protected by the panel's login and the
allowlist, which is the intended design. If you would rather the whole thing be
invisible to the public internet, Vercel → Settings → Deployment Protection →
Vercel Authentication → *All Deployments*. That adds a Vercel login in front of
the app's own, which is belt and braces rather than a fix for anything.

Changing an environment variable does **not** redeploy on its own. Change it,
then redeploy, or the running build keeps the old value — including
`ADMIN_ALLOWED_EMAILS`, which is exactly the variable you would change in a
hurry.
