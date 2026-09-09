# Deploying the panel to Vercel

**Done — live at <https://castrol-pipeline-admin-panel.vercel.app>** (scope
`castroinfra`, Root Directory `panel`, deployed and verified 2026-09-09:
sign-in works, all four pages render live data, and the secret key is confirmed
absent from the browser bundle).

Kept as the record of what was set and why, and as the runbook for doing it
again — a second environment, a rebuild, or a handover.

Ten minutes, five steps.

---

## 0. The panel must be on the branch Vercel builds — done

Vercel builds Production from the repository's **default branch**. `main` had
no `panel/` directory until PR #3 landed, so importing the project before that
merge would have failed before the build started, complaining that the Root
Directory did not exist — which reads like a typo rather than a missing merge.

Settled: `main` carries `panel/` as of the merge. Nothing to do here.

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

## 5. Create the admin account, and close the door behind it

Supabase → **Authentication → Users → Add user**: the address from
`ADMIN_ALLOWED_EMAILS`, a password, **Auto Confirm User** on.

This is the ONLY place a password is ever set or changed. The panel has no
account page and will not be getting one (decided 2026-09-09), so every
password is held by whoever typed it here and rotated from this same screen.
Supabase keeps a bcrypt hash and does the checking itself; the panel forwards
the password and gets back a session, so nothing in this repo stores, logs or
can read one.

`ADMIN_ALLOWED_EMAILS` is comma-separated, so giving someone at the client
their own login is two steps and no code change: add their address to the
variable, add a user here. Worth it over sharing one credential -
`job_reports.reported_by` records whoever was signed in, so a shared account
means you can never tell who reviewed what, and revoking one person means
changing a password everyone uses.

Then Supabase → **Authentication → Sign In / Providers → Email** → turn
**"Allow new users to sign up" OFF**.

With password auth and sign-ups open, anyone who finds the URL can mint an
account. The allowlist still stops them reading anything — but that is the
second line of defence, and you do not want to be standing on it.

**There is no Supabase URL configuration step**, and there used to be. Emailed
links needed one — a magic link, then a password-recovery link, both of which
land on a route that exchanges a code for a session. Neither is in use: sign-in
is email and password, and a forgotten password is reset directly in Supabase →
Authentication → Users, which sends nothing and needs no route. `/auth/callback`
has been removed rather than left sitting there as an unauthenticated endpoint
serving a flow nobody uses.

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
