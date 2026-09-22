# CLAUDE.md — Castrol MAGNATEC video pipeline

Batch video factory. Pull mechanic submissions from the client's export API,
generate a personalised vertical promo video each, host it, POST the URL to the
client's delivery webhook.

Read [`docs/TECH_DESIGN.md`](docs/TECH_DESIGN.md) before changing anything
structural. [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) holds scope, client
decisions and risks.

---

## Accounts — read this first

**GitHub, Supabase, Vercel and the AI provider are dedicated logins, separate
from every other BeHooked project.** Never reuse a credential, project ref or
CLI profile from `BeHooked/Webapp` or `behooked_studio_backend` for those.

**AWS is the exception, and this paragraph used to get it wrong.** It said AWS
was a separate login too. It is not: `castrol-local` lives in account
**872515254882**, which is the shared BeHooked account — the same one running
`behooked-studio-backend-prod`, `hooked-micro-apps`, `hooked-nodeflow` and
`orchestrator-prod` (plus `caption-studio`, stopped), and holding
`behooked-dokploy-backups`, `cached-brolls` and `caption-studio` next to our
bucket. What IS dedicated is the **IAM user**
and the **bucket**, and that is the whole of the separation. Anything created in
this account is created next to four running production services, so scope it
by name and by security group and assume nothing is yours alone. Corrected
2026-09-15, found while provisioning the EC2 worker.

- **Git remote:** SSH alias `github-castrolinfra` (account `castrolinfra-sys`).
  The repo has a local `user.name` / `user.email` set to match — do not run git
  commands that would fall back to the global identity.
- **`gh` CLI is authenticated as `nachimore`, the wrong account.** Do not use
  `gh` for anything that writes to this repo.
- **Supabase / AWS / Vercel / the AI providers:** credentials live in `.env`
  only.
  `.env.example` documents every variable.
- **`castrol-local` cannot provision, by design.** It holds S3 object
  read/write plus EC2 *read*. It is denied `ec2:CreateSecurityGroup` and all of
  IAM, so standing up infrastructure needs a separate admin session — not a
  wider policy on the key that sits in `.env` on the worker itself.

---

## Commands

```bash
uv sync
```

### Prototype — one video end to end (Phase 0 path)

→ [`spikes/prototype.py`](spikes/prototype.py). Imports the card and ffmpeg
settings from [`stages/media.py`](src/castrol_pipeline/stages/media.py).

```bash
uv run python spikes/prototype.py --plate spikes/in/plate.png --photo spikes/in/mechanic.jpg --script spikes/in/script_spoken.txt --out spikes/out/run1 --name "Raju Shetty" --workshop "Shetty Motors" --address "Andheri, Mumbai" --phone "9898989898"
```

One step only (`image` | `audio` | `video` | `composite`):

```bash
uv run python spikes/prototype.py --out spikes/out/run1 --only audio --script spikes/in/script_spoken.txt
```

Steps are resumable via `spikes/out/<run>/_state.json`. To force a completed
step to re-run, delete its key from that file. Never re-run the video step
casually — it is $0.036 per second of output on standard, $0.072 on pro.

### Pipeline

→ [`cli.py`](src/castrol_pipeline/cli.py) dispatches all of these;
`seed-job` is [`seed.py`](src/castrol_pipeline/seed.py), the rest run through
[`orchestrator.py`](src/castrol_pipeline/orchestrator.py).

```bash
uv run castrol doctor
```

**The whole run, unattended.** This is what the EC2 timer fires at 00:00 and
12:00 IST and the only command the server executes: lock, pull the window,
repair half-created intake rows, reopen suppressed deliveries, schedule, then
work and wait until every job is terminal or the deadline hits. See
[`deploy/`](deploy/README.md). Holds a database advisory lock, so a second
cycle starting on top of a running one exits instead of doubling the load on a
paid vendor. **SPENDS.**

```bash
uv run castrol cycle
```

Finish what is already in the database without pulling anything new:

```bash
uv run castrol cycle --no-fetch
```

One video by hand — creates the rows and lands the photo in S3, runs nothing.
Idempotent on (photo, phone). `--address` is what the CARD prints; what the
voice SAYS defaults to the last segment of it, override with `--spoken-place`.

```bash
uv run castrol seed-job --photo spikes/in/mechanic2.jpg --plate spikes/in/plate_bg2.png --uniform-ref uniform/u1_tshirt.png --name "Amit Kumar" --workshop "Ganesh Car Service" --address "Beturkar Pada, Opposite New National Hospital, Andheri" --phone 9773128990 --uniform u1_tshirt --background bg2_dark_sedan
```

Register plate artwork for one combination, and the uniform reference the image
edit uses as its third input. Append-only (invariant 31): the current row is
retired and a new one inserted. Free, runs nothing.

```bash
uv run castrol register-plate --plate plates/plate_02.png --uniform u1_tshirt --background bg2_dark_sedan --uniform-ref uniform/u1_tshirt.png --approved-by "new artwork 2026-09-11 - Castrol-only chest, plain sleeves"
```

**One job, by any identifier** — job id, submission id, client row `id`,
WhatsApp or card phone in any shape, `mechanic_id`, stage run id, vendor task
id. `show`, `events` and `redo` accept the same. → [`jobref.py`](src/castrol_pipeline/jobref.py)

```bash
uv run castrol find 9773128990
```

```bash
uv run castrol find --status failed --since 2026-09-16
```

Drive that ONE job to a finish — claims and polls only its runs, so nothing else
queued is paid for. Lists the paid stages it may submit and asks. Resumable:
run it again after Ctrl-C or a reboot. `--retry` re-attempts terminally failed
stages (invariant 34). **SPENDS.** → [`jobrun.py`](src/castrol_pipeline/jobrun.py).
On EC2 it is `castrol run …` through [`deploy/castrol`](deploy/castrol), and
`castrol --bg run … --yes` to survive the SSM session closing.

```bash
uv run castrol run 9773128990
```

**The rehearsal** - `STAGE_MODE=mock`: real export, real free stages, only
audio/image/video faked, failures injected by `MOCK_FAILURES`, nothing spent or
sent. Shares the real database, so the code keeps the two apart: mock refuses
unless `S3_PREFIX=castrol-dryrun/` and delivery is off; real refuses while any
`vendor='mock'` run or `castrol-dryrun/` asset remains. On EC2 every rehearsal
command is `castrol --dryrun ...` (a flag, because `sudo` drops env vars). Step
by step: [`deploy/README.md`](deploy/README.md#rehearsal-before-launch-dry-run).
-> [`stages/mocks.py`](src/castrol_pipeline/stages/mocks.py), [`dryrun.py`](src/castrol_pipeline/dryrun.py)

Empty the job tables before launch. Keeps plates, `vendor_limits`, panel logins
and the ledger; asks for `RESET`:

```bash
uv run castrol reset-for-launch
```

```bash
uv run castrol drain
```

```bash
uv run castrol work --stage audio
```

```bash
uv run castrol poll --watch
```

Re-run a stage on a finished job — a reworded avatar prompt, a corrected model
id. Demotes that stage and everything downstream to `skipped`, reopens the job,
re-schedules. **SPENDS on the next `work`**; shows the last attempt's cost and
asks first.

```bash
uv run castrol redo <job-id> --stage video
```

```bash
uv run castrol schedule
```

```bash
uv run castrol intake --from 2026-09-01 --to 2026-09-01
```

### Inspecting a run

→ [`cli.py`](src/castrol_pipeline/cli.py) and
[`seed.py:describe`](src/castrol_pipeline/seed.py), reading `job_costs` and
`job_events` from [`0004`](supabase/migrations/0004_runtime_observability.sql).
`job_costs.cost_usd` is what was actually BILLED — refunded attempts are
excluded from it and carried separately as `refunded_usd`
([`0013`](supabase/migrations/0013_refunded_runs.sql), invariant 24).

```bash
uv run castrol show <job-id>
```

```bash
uv run castrol events <job-id>
```

```bash
uv run castrol costs
```

```bash
uv run castrol report
```

### Admin panel

→ [`panel/`](panel/). Next.js, **live** at
<https://castrol-pipeline-admin-panel.vercel.app> — Vercel scope `castroinfra`,
Root Directory `panel`. Deployed and verified 2026-09-09; the how and the
reasoning are in [`panel/DEPLOY.md`](panel/DEPLOY.md).
Reads the pipeline's tables directly; the one thing it writes is `job_reports`.

**It is a CLIENT-facing surface, not our operations console.** It must never
show cost, vendor, model id, stage, retry attempts, or an internal error code —
the metric it reports is DURATION. Specifically the **render** length, which is
what the provider billed us and what the client is billed on — not the shorter
trimmed file that ships (migrations `0010` and `0014`). That line is held
structurally rather than by care: the pages read
[`job_usage` / `daily_usage`](supabase/migrations/0007_usage_views_for_the_panel.sql),
views with no cost or vendor column in them, and `lib/format.ts` has no money
formatter to reach for. `lib/reasons.ts` turns `"video: VENDOR_TIMEOUT"` into a
sentence, falling back to a generic line rather than to the raw string — a
fallback that leaks does it exactly when something new breaks. Pages: Jobs
(searchable by mechanic id or WhatsApp number, filterable by date), Failures,
Submissions, Usage.

```bash
cd panel && npm install && npm run dev
```

Needs `panel/.env.local` (see `panel/.env.local.example`) — its own file, not
the pipeline's `.env`. **RLS is deny-all with no policies, so a signed-in
user's own token reads nothing**: every query runs server-side with the secret
key, and `ADMIN_ALLOWED_EMAILS` is therefore the only access control in the
product.

Auth is Supabase email + password. **Accounts are created in the Supabase
dashboard — the panel has no sign-up, and public sign-ups must stay disabled**,
or anyone could mint an account. (The allowlist would still stop them reading
anything, but that is the second line, not the first.) The form posts to a
server action, so the password is never client component state. The gate uses
`getUser()`, never `getSession()`, because a session cookie is forgeable by the
browser, and it lives ONLY in `middleware.ts` — signing in proves identity, the
allowlist decides access, and a second check elsewhere would be one more thing
to keep in step.

**`currentAdmin()` does NOT call `getUser()`, and that is not an oversight.**
`getUser()` is a network round trip to Supabase Auth — that is the whole reason
it is trusted over `getSession()` — measured at ~60ms. The root layout called it
on every render to find out whose email to print in the header, so each page
view paid that latency TWICE before fetching a row. Middleware now publishes its
verified verdict on the `x-castrol-admin` request header
([`lib/env.ts:ADMIN_EMAIL_HEADER`](panel/lib/env.ts)) and
[`lib/session.ts`](panel/lib/session.ts) reads it. Do not "restore" the second
call.

The gate is unchanged and is still middleware alone: `currentAdmin()` never was
the gate, it only decided whether to draw the header. The header cannot be
forged — middleware `delete`s any inbound copy unconditionally before setting
the verified one, and its matcher runs on every path that renders anything, so
no render is reachable without passing through it. Verified by curl: a forged
`x-castrol-admin` carrying a real allowlisted address still 307s to `/login`.

**The matcher's exclusion list is a security boundary, not housekeeping.** It
holds `_next/static`, `_next/image`, `favicon.ico` and `icon.svg` — and
`icon.svg` had to be added, because the app-router icon is served from a real
route and was therefore gated like a page: signed out, the browser asked for the
favicon and got a 307 to `/login`, so the one page a signed-out visitor sees was
the one with no icon. Nothing may be added to that list unless it is a static
asset with nothing to leak.

**There is no emailed-link route.** `/auth/callback` is gone: magic link is
replaced and password recovery is not used — a forgotten password is reset in
Supabase → Authentication → Users, which sends nothing. So there is no Supabase
URL configuration to keep in sync, and no unauthenticated endpoint minting
sessions from a code for a flow nobody uses.

`ADMIN_ALLOWED_EMAILS` lives in the APP's environment — `panel/.env.local`
locally, Vercel's env vars in production. It is not a Supabase setting and
Supabase never sees it. Supabase decides who can authenticate; this decides who
is let in once they have.

**Passwords are ours to hold, and the panel deliberately cannot change one.**
Decided 2026-09-09. There is no account page and no self-service rotation: a
password is set in Supabase → Authentication → Users and reset there. Do not add
a change-password page - it was considered and declined, not overlooked.

The consequence is deliberate but worth stating: whoever sets a client's
password knows it, and rotation is a dashboard action rather than something the
client can do alone. Supabase stores only a bcrypt hash and verifies it itself -
the panel forwards the password to `signInWithPassword` and receives a session,
so nothing here ever stores, logs or can read one.

#### The look — rebuilt 2026-09-15

**Light theme. The previous dark one is gone, not toggleable.** Rebuilt rather
than inverted: a palette tuned to glow on black has the wrong saturation to sit
on white. The ground is off-white (`--bg`) and CARDS are pure white, so elevation
reads as *lighter* than the page — that is what lets the sticky table header
separate itself without a heavy rule under it.

**There are TWO greens and they are not interchangeable.** Collapsing them is
the obvious-looking cleanup and it silently reverts a measured decision:

| token | value | job |
|---|---|---|
| `--accent` | `#014d26` | INK — links, focus ring, active nav, success pills |
| `--accent-mark` | `#00843d` | FILL — chart bars, the brand mark |

`--accent` is 9.6:1 on the ground and excellent as text. As a *mark* colour it
FAILS both the lightness band and the chroma floor — across a wide bar it stops
reading as green and reads as dark slate. That verdict came from the `dataviz`
Claude skill's `scripts/validate_palette.js` (it ships with the skill, not with
this repo), run as `validate_palette.js "<hex>" --mode light`: `#014d26` fails
two checks, `#00843d` passes all of them. Re-run it before changing either
green rather than judging a fill colour by eye.

**`--bad` is failure and nothing else.** The old palette used red for both the
brand mark and errors, so a healthy page and a broken one were the same colour.

**Type is `next/font`, self-hosted, three faces.** Barlow (body/data), Barlow
Semi Condensed (every uppercase micro-label — nav, column heads, stat captions,
form labels), IBM Plex Mono with slashed zero (identifiers only, so `0` and `O`
differ when someone eyeballs a WhatsApp number against a spreadsheet). Barlow is
drawn from industrial and transport signage, which is the subject's own
vernacular; it is also low-contrast, which is what survives 14px across a
500-row table.

The next/font CSS variables are named `--font-display-src` / `--font-mono-src`
**deliberately**. Naming them `--font-display` / `--font-mono` makes the `:root`
declarations self-referential, and CSS drops a cycle silently — taking the
fallback chain with it while still *looking* correct, because next/font injects
its own fallback face.

Two things that look like omissions and are not:

- **No `content-visibility` on table rows.** These tables are auto-layout, so
  skipped rows stop contributing to column widths and the columns visibly jitter
  as you scroll. Considered and declined.
- **No `next/dynamic` around the chart.** It is a client component and Next
  already splits those per route. The build output is the proof: `/usage` 205 kB
  First Load, every other route 102–107 kB. recharts never leaves that page.

**Every duration is CEILED to a whole second, and shown without a decimal.**
Changed 2026-09-15, replacing the one-decimal form. Ceiling is not cosmetic:
the provider bills per output second and rounds UP, so a 24.2s render is billed
as 25s and
"24.2s" was a number the client is not charged for and that matches no invoice
line. Rounding to NEAREST would be worse than the decimal, because it would
sometimes report less than was billed. `lib/format.ts` is the only place this
is decided - `secs()`, `duration()` and `durationParts()` all ceil, and the
Usage page's three hand-rolled `toFixed(1)` call sites were folded back into it
so they cannot drift again.

**The ceiling that MATTERS is in the view, not in `format.ts`.** Migration
[`0014`](supabase/migrations/0014_ceil_the_billed_second.sql) moved it there,
because two ways of under-recovering could not be fixed in the panel at all:

- `0010` rounded `video_seconds` to one decimal IN SQL, and rounding can cross
  an integer boundary downward — a true 27.04s became `27.0`, which ceils to 27
  where the provider billed 28. The panel cannot recover a second SQL already
  discarded.
- Totals ceiled the SUM instead of summing the ceilings. The provider issues
  one charge per render, each rounded up on its own, so the nine renders of
  2026-09-14 bill
  at 250s; ceiling their 245.1s total gave 246s, a figure matching no invoice.

`daily_usage` therefore sums already-ceiled integers and its re-round from
`0010` is gone. `Math.ceil` in `format.ts` stays as a no-op guard for the day
someone edits that expression back.

**Jobs fetches `limit(501)` and drops the 501st.** It does not use
`{ count: "exact" }` — that makes PostgREST run a real `COUNT(*)` over the
filtered view on every load, a second scan to print a total nobody acts on.
"First 500" answers the only question that matters.

**Jobs STREAMS, and the page itself fetches nothing.** It returns the shell
and the filter chips immediately and awaits the table inside a `<Suspense>`
boundary, because awaiting the whole query first means one slow read holds up
the entire response — which on a client-side navigation is indistinguishable
from a broken app: the URL changes, the chip spinner turns, nothing arrives.

That boundary is **keyed on `range:q`** and the key is load-bearing. A `<Link>`
that alters only the query string re-renders the SAME route segment, so
`loading.tsx` never mounts and no skeleton appears on a filter change; changing
the key makes React show the fallback. The fallback renders the REAL `Filters`,
not placeholders, so clicking another range mid-load does not hit a dead strip.

**Every panel query carries a 12s deadline** — `queryDeadline()` /
`QUERY_TIMEOUT_MS` in [`lib/db.ts`](panel/lib/db.ts), passed to
`.abortSignal(...)`. supabase-js puts no timeout on its fetch, so a stalled
read waits forever and so does the render above it. Past 12s the query errors
and surfaces through `<Problem>` — a sentence and a Retry, instead of an
indefinite hang. Measured healthy reads are 100–300ms, so this is a failure
valve, not a budget.

**Filter-chip `<Link>`s set `prefetch={false}`, measured rather than cautious.**
Each chip otherwise fired its own RSC request on every page load — six requests,
each running middleware and so each paying a `getUser()` round trip — to warm a
loading-shell cache that a `searchParams` navigation never reads. `useLinkStatus`
supplies the feedback the browser used to give for free before these became
`<Link>` rather than `<a>`. Separately, `<Link>` prefetch is **disabled in
development** regardless, which is why `npm run dev` feels slower than the
deployed panel and is not evidence of a problem.

Skeletons (`loading.tsx` per route, plus `pending.tsx`) exist because every page
is `force-dynamic` against Supabase, so there is always a real wait.

**The 404s are split on purpose.** The app-wide `not-found.tsx` carried
job-specific copy, so every unknown URL was told there was no job with that
reference; the job wording now lives in `jobs/[id]/not-found.tsx`, where
`notFound()` actually fires.

**Titles template per page and the whole app is `noindex`.** `layout.tsx` sets
`title.template = "%s · Castrol pipeline"` and each page sets only its own half,
which is what makes four open tabs readable. `robots: { index: false }` is a
meta tag and NOT a `robots.txt` Disallow, deliberately: a Disallow stops a
crawler reading the page, which stops it seeing the noindex, so the URL can
still surface from an external link. Middleware redirects every page to
`/login`, but `/login` itself is crawlable on a public hostname.

### Checks

```bash
uv run pytest
```

```bash
uv run ruff check .
```

### Docker / CI

→ [`Dockerfile`](Dockerfile), [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

Every push and PR runs `ruff` + `pytest`; a push to `main` or a `v*` tag also
builds and pushes the worker image, gated on those passing. The suite needs no
`.env` — every `Settings` field is defaulted or optional — so CI holds no
pipeline credentials at all.

**Docker Hub is reached by access token, never by an OAuth account link.** The
Docker Hub account is not linked to any BeHooked GitHub identity and must not
be; a token in GitHub secrets is what bridges them. Set in the repo's
Settings → Secrets and variables → Actions:

| | Name | Value |
|---|---|---|
| secret | `DOCKERHUB_USERNAME` | Docker Hub account name, not an email |
| secret | `DOCKERHUB_TOKEN` | access token, Read & Write scope |
| variable | `DOCKERHUB_IMAGE` | full repo — `gethooked/castrol-video-pipeline` |

Tags: `latest` and `main-<sha>` on `main`, semver on `v*`. **The EC2 worker
tracks `latest` and pulls on every run** — continuous deploy, decided
2026-09-15, reversing the earlier "deploys pin the sha tag". The spend argument
for pinning did not survive checking: `cycle._schedule_all()` selects
`status NOT IN ('completed','cancelled')`, so a changed `input_hash` never
re-renders a finished video — only jobs still open when the new image first
runs, and the pull lands between cycles rather than mid-batch. What the choice
actually costs is a review gate: a merge reaches a paying worker with no
staging and no visual check, and `pytest` green does not mean a render looks
right. Pin a `main-<sha>` in `/etc/castrol-image.env` to freeze deliberately.

The image carries no credentials. Supply them at run time:

```bash
docker run --rm --env-file .env gethooked/castrol-video-pipeline:main-331b386 work --stage audio
```

Two container-only hazards, both covered by the workflow's smoke test:
`ffprobe` must exist or the video stage misbills (invariant 12), and
`fonts-dejavu-core` must be installed or `render_card` silently falls back to
Pillow's bitmap default. Note the card renders in **DejaVu** here and in Segoe
UI on Windows — the metrics differ, so verify a card out of the container
before trusting a layout that was approved off a local render.

### Balances — check before any run that spends

→ rates are pinned in [`common/budget.py`](src/castrol_pipeline/common/budget.py);
daily caps live in `vendor_limits`.

```bash
curl -sS https://api.apimart.ai/v1/user/balance -H "Authorization: Bearer $APIMART_API_KEY"
```

```bash
curl -sS https://api.kie.ai/api/v1/chat/credit -H "Authorization: Bearer $KIE_API_KEY"
```

The image gateway reports USD directly (1 credit = $0.10). The video gateway
reports its own credits at roughly **207 per USD** — measured 2026-09-14, not
inferred: a batch
of nine standard renders totalling 250 billed output seconds consumed exactly
1864 credits, which at $0.036/s is 7.456 credits per second. The older figure
of 166 came from reading a refusal message and was wrong by a quarter.

### Client export

→ [`intake/export_client.py`](src/castrol_pipeline/intake/export_client.py).

```bash
curl -sS "https://capi.letschbang.com/api/submissions/export/vendor?from=2026-09-02&to=2026-09-03" -H "apikey: $CLIENT_EXPORT_API_KEY"
```

Returns **CSV**, not JSON. Rate limit 100 / 900s.

### Migrations

Forward-only numbered SQL in `supabase/migrations/`, applied in order. There
are no down migrations. Use the apply script — one file per invocation, the
DDL and its ledger row in a single transaction:

```bash
uv run python scripts/apply_migration.py supabase/migrations/0005_admin_review_and_export_copy.sql
```

The script registers what it applies in `supabase_migrations.schema_migrations`,
keyed on the file's name so a re-run cannot claim a second apply. It did not
always: 0001–0003 were registered by the Supabase tooling and 0004–0005 were
not, and a HALF-populated ledger is worse than none, because `supabase db push`
reads it and would treat applied migrations as pending. Both were backfilled;
0001–0014 are now applied and registered.

### AWS

One-time admin setup only — see [`infra/README.md`](infra/README.md) and
[`infra/s3-lifecycle.json`](infra/s3-lifecycle.json). The pipeline's IAM user
(`castrol-local`) can read and write objects but cannot delete them or change
bucket configuration, which is deliberate: retention belongs to lifecycle
rules, not application code.

---

## Where things live

| Aspect | File |
|---|---|
| Every `castrol <cmd>` | [`src/castrol_pipeline/cli.py`](src/castrol_pipeline/cli.py) |
| Claiming, retries, poller, **all writes to `jobs`** | [`orchestrator.py`](src/castrol_pipeline/orchestrator.py) |
| The unattended run: lock, window, orphan repair, wait loop | [`cycle.py`](src/castrol_pipeline/cycle.py) |
| Any identifier → job | [`jobref.py`](src/castrol_pipeline/jobref.py) |
| One job to a finish (`castrol run`), per-job lock | [`jobrun.py`](src/castrol_pipeline/jobrun.py) |
| The `castrol` command on the EC2 box | [`deploy/castrol`](deploy/castrol) |
| Rehearsal: mock paid stages, failure injection | [`stages/mocks.py`](src/castrol_pipeline/stages/mocks.py) |
| Rehearsal/real guard, `reset-for-launch` | [`dryrun.py`](src/castrol_pipeline/dryrun.py) |
| systemd units + the EC2 runbook | [`deploy/`](deploy/README.md) |
| **The EC2 deployment as built** — ids, decisions, what was verified | [`docs/EC2_DEPLOYMENT.md`](docs/EC2_DEPLOYMENT.md) |
| The eight real stages | [`stages/real.py`](src/castrol_pipeline/stages/real.py) |
| Stage protocol + the DAG | [`stages/base.py`](src/castrol_pipeline/stages/base.py) |
| Anything that talks to a provider | [`stages/vendors.py`](src/castrol_pipeline/stages/vendors.py) |
| Anything local and free — **the card**, ffmpeg, ffprobe | [`stages/media.py`](src/castrol_pipeline/stages/media.py) |
| No-spend doubles (`USE_STUB_STAGES=true`) | [`stages/stubs.py`](src/castrol_pipeline/stages/stubs.py) |
| **The only path to a paid vendor** | [`common/budget.py`](src/castrol_pipeline/common/budget.py) |
| S3 keys, presigning, copy, `cdn_url` | [`common/s3.py`](src/castrol_pipeline/common/s3.py) |
| Pool, `SKIP LOCKED` claim, `mark_*`, reaper | [`common/db.py`](src/castrol_pipeline/common/db.py) |
| `input_hash` builders, canonical JSON | [`common/hashing.py`](src/castrol_pipeline/common/hashing.py) |
| Durable `job_events` + credential scrubbing | [`common/events.py`](src/castrol_pipeline/common/events.py) |
| structlog to stdout | [`common/logging.py`](src/castrol_pipeline/common/logging.py) |
| Reject codes + stage error taxonomy | [`common/errors.py`](src/castrol_pipeline/common/errors.py) |
| **The script text** and spoken overrides | [`prep/script.py`](src/castrol_pipeline/prep/script.py) |
| Phone, address, numerals for speech | [`prep/normalise.py`](src/castrol_pipeline/prep/normalise.py) |
| `outfit` + `background` → plate | [`prep/plates.py`](src/castrol_pipeline/prep/plates.py) |
| Plate + uniform reference registration | [`seed.py:register_plate`](src/castrol_pipeline/seed.py) |
| Export pull (**CSV, BOM**), validation, dedupe | [`intake/`](src/castrol_pipeline/intake/) |
| Create one job by hand | [`seed.py`](src/castrol_pipeline/seed.py) |
| Every env var and pinned model id | [`config.py`](src/castrol_pipeline/config.py) / [`.env.example`](.env.example) |
| Schema, budget function, RLS | [`supabase/migrations/`](supabase/migrations/) |
| Lifecycle rules, bucket posture | [`infra/`](infra/) |
| The standalone one-video script | [`spikes/prototype.py`](spikes/prototype.py) |
| Admin panel (Next.js, Vercel) | [`panel/`](panel/) |
| The panel's only DB handle — secret key, bypasses RLS | [`panel/lib/db.ts`](panel/lib/db.ts) |
| Who may open the panel, and the `x-castrol-admin` header | [`panel/middleware.ts`](panel/middleware.ts) |
| Palette, type scale, every design token | [`panel/app/globals.css`](panel/app/globals.css) |
| The panel's only write | [`panel/app/actions.ts`](panel/app/actions.ts) |
| Duration-only views the panel reads | [`0007`](supabase/migrations/0007_usage_views_for_the_panel.sql), [`0008`](supabase/migrations/0008_job_usage_video_url.sql) |

Full annotated map with per-file descriptions: [`README.md`](README.md#where-things-live)
and [`docs/TECH_DESIGN.md` §3](docs/TECH_DESIGN.md).

---

## Inputs per video

| Input | Source | Used for |
|---|---|---|
| Plate (uniform + background) | frozen, chosen by export `outfit` + `background` | the scene |
| Uniform reference | frozen, registered on the plate row | garment detail, third input to the image edit |
| Mechanic photo | export `image_url` | face/build swapped onto the plate |
| Name | export `user_name` | **spoken** + card |
| Workshop name | export `workshop_name` | **spoken** + card |
| Location | export `address` | **spoken** + card |
| Delivery phone | export `whatsapp_number` | **delivery key only**, never spoken, never printed |
| Card phone | export `mechanic_phone_number` | **card only**, never spoken |

Only name, workshop and location vary inside the script. The two phone fields
are two different numbers doing two different jobs, confirmed by the client
2026-09-08: `whatsapp_number` is what we POST back as `phone` and is the join
key the client relays on; `mechanic_phone_number` is the contact number printed
in the card's green panel. Neither is ever spoken.

They are NOT interchangeable and must not be collapsed back into one column.
`submissions.phone_e164` is the delivery key (`whatsapp_number`);
`card_phone_e164` is what the card prints (`mechanic_phone_number`).

**`stages/real.py:_card_fields` read the wrong one until 2026-09-15**, so every
card printed the mechanic's WhatsApp number burned into a video that gets shared
around. The column existed and intake populated it correctly; only the renderer
was wrong, which is why nothing looked broken. It now reads `card_phone_e164`
and falls back to `phone_e164` only when that is null — which is the `seed-job`
case, where a single `--phone` supplies both and there is nothing to confuse.

That fix is NOT a `card_template_version` bump: the phone value sits inside
`media.card_payload`, which is already in the composite `input_hash`, so a job
whose printed number actually changes re-burns on its own. The version is for
what the hash cannot see.

`mechanic_id` would be the natural primary key but is not reliable enough to
use as one; the export's own `id` is.

---

## Cost

```
standard   cost = $0.014 + seconds x $0.036        (25s ~ $0.91)
pro        cost = $0.014 + seconds x $0.072        (25s ~ $1.81)
```

Full tables in [`docs/COST_PER_VIDEO.md`](docs/COST_PER_VIDEO.md), including
rupees at ₹100 = $1. The video step is ~96% of it and bills per output second,
so runtime is the only lever worth pulling. The provider ceils to whole
seconds, so 24.8s bills as 25s — negligible at a 25s script, a 100% overcharge on a 1s clip.

**`ai-avatar-pro` is the 1080p route and is NOT ruled out.** It was declined on
2026-09-10 as too expensive, then reinstated on **2026-09-12** when the client
asked for 1080p: `kling/ai-avatar-standard` returns 720x1280 whatever it is fed
and `kling/ai-avatar-pro` returns 1072x1920. There is no resolution parameter on
either endpoint — the model id is the whole switch. The earlier "720x1280 is the
ceiling" claim measured a *standard* render and generalised it into a property
of the model; it is a property of the TIER. Do not cite the old decision as
standing.

**`VIDEO_MODEL_ID` alone decides both what is submitted and what is reserved.**
`Settings.video_is_pro` derives from it, so the budget cannot disagree with what
was sent. There is no separate `VIDEO_USE_PRO` — there was, and setting one
without the other silently under-reserved by 2x on the only expensive step.

**The rates agreed on 2026-09-15 and this paragraph used to say they did
not.** `common/budget.py` was corrected in `cf00e4a` to $0.036/$0.072 and now
matches the tables above; the claim that it pins $0.04/$0.08 and over-reserves
by 11.1% outlived the fix by five days and was still being quoted as current.
`job_costs` reads true. Reconcile against the provider dashboard after any
batch anyway — `docs/TALKING_HEAD_PIPELINE_REFERENCE.md` still carries $0.04/s,
correctly, because it records what the OTHER stack measured and is not our
config.

**Caps are `vendor_limits`, and `daily_cost_cap_usd` is the one that binds.**
Raised 2026-09-15 by migrations `0011` (cost) and `0012` (calls) to **$5000 on
`kie_video`, $500 on the other two**, with the call caps lifted to match so the
cost cap trips first everywhere — 5000 / 40000 / 25000 respectively. 0011 alone
had inverted this: it left the call caps at 200/600/600, which made *those* the
real ceiling at ~$211/day on the video lane while the number anyone would read
said $5000.
0012 restored 0002's design. Both cheap vendors had to move too, because every
video costs one image call and one TTS call — a 600-call cap on either would
have halted the pipeline at 600 videos, well under the video lane's ~4,732, and
moved the binding constraint to stage A or B without saying so.

Worst case is now **$6000/day** against an observed ~40 videos (~$44). These
caps are a runaway guard and nothing else; what actually keeps spend honest is
invariant 3, `require_cost_estimate` on the video vendor, and per-attempt cost
recording.
The cap day is **IST**, and both timer cycles fall inside one — a heavy 00:00
run starves the 12:00 one.

**The caps count refunded calls; the cost view does not.** That asymmetry is
deliberate (0013, invariant 24): a guard that forgave every failure is one a
retry loop can walk straight through, while a quote built on reservations
rather than on billings overstates the bill.

## Invariants

These are the things that break silently and expensively. Do not relax them
without changing the tech doc first.

**1. The Azure SAS photo URL is opaque bytes.**
*`intake/media.py`*
Pass `image_url_raw` to the HTTP client verbatim. Never `quote`/`unquote` it,
never form-decode it, never rebuild it from parsed components, never route it
through a URL-normalising client. Azure signs over exact bytes; any
normalisation returns 403 that reads like a permissions failure. Encoding is
inconsistent *within a single URL*, so it looks wrong — it is not.

**2. Validate magic bytes, not status codes.**
*`intake/media.py`, `stages/vendors.py:_sniff_is_image`*
A permissions failure can return HTTP 200 with an HTML body. Without a
magic-byte check, that writes login pages into S3 as `.jpg`.

**3. Every paid vendor call goes through `common/budget.py`.**
*[`common/budget.py`](src/castrol_pipeline/common/budget.py), over `reserve_vendor_call()` in `0002_budget_and_seed.sql`*
It calls the `reserve_vendor_call()` Postgres function and refuses on `false`.
No stage may reach a vendor by any other path. This is the only thing between a
retry bug and a real bill.

**4. `input_hash` covers model ids and prompt/template versions.**
*`common/hashing.py`, consumed by `orchestrator.schedule_ready`*
That is what makes changing a model id regenerate instead of skip. Canonical
JSON lives in `common/hashing.py` and nothing else may serialise for hashing.

**5. No generative model ever renders text.**
*`stages/media.py:render_card`*
The personalisation card is a deterministic Pillow render burned in with ffmpeg
after video generation. Keep it that way.

**6. Geometry is preserved in stage B.**
*`stages/vendors.py:image_prompt()`, pinned by [`tests/test_image_prompt.py`](tests/test_image_prompt.py)*
The card sits at a fixed pixel position. If person replacement shifts subject
scale or the belt line, the card lands on the mechanic's hands. Change /
Preserve / Constrain prompt structure is deliberate.

The **uniform reference** is the sharpest way to break this, and the reason the
prompt is now built rather than fixed. A second picture of the same garment,
framed differently, is an invitation to reframe - so its clause says, in the
clause itself, that fit, size and position come from the first image and that
the flat shot's own framing is irrelevant. It also opens by putting the uniform
OUTSIDE the one change: it is still one generation and one edit, and a clause
that reads as a second instruction contradicts `Make EXACTLY ONE change` two
paragraphs above it - the same self-argument invariant 29 records degrading the
avatar prompt. A plate with no reference gets the
two-image prompt, which must never mention a third image: an ordinal pointing
at an input that was not sent is a prompt the model has to guess at.

**7. Async stages submit and release.**
*`stages/real.py` (ImageStage, VideoStage), `orchestrator.poll_once`*
Stage C writes `vendor_task_id` and returns. The poller reconciles. Never block
a worker on a vendor poll.

**8. Rejected rows are not repaired.**
*`intake/validate.py`, `common/errors.py:RejectCode`*
Intake rejects with a stable code. A repaired row is a row whose output nobody
can explain.

**9. Timestamp format is pinned, never inferred.**
*`config.py:export_timestamp_format`, `intake/export_client.py`*
`EXPORT_TIMESTAMP_FORMAT` in env. The raw string is also stored so a wrong
format can be reparsed without re-pulling.

**10. Real mechanic photos never enter git.**
*`.gitignore`*
`spikes/in/`, `spikes/out/` and media extensions are gitignored. This is
personal data — face photos joinable to phone numbers.

---

**11–26 come from
[`docs/TALKING_HEAD_PIPELINE_REFERENCE.md`](docs/TALKING_HEAD_PIPELINE_REFERENCE.md)**,
prod-measured evidence from the existing BeHooked backend — each one a failure
someone there already paid for. **27 onwards were measured on THIS pipeline**,
and are numbered in the order they were learned rather than grouped by subject.
They are listed in numeric order; cite them by number.

**11. Ship MP3 to the avatar model, never WAV.**
*`stages/media.py:to_mp3`, called by `AudioStage`*
`"Audio size is too large"` is a byte limit, not a duration limit. Every
observed failure was a WAV — a 37s WAV failed while a 53s WAV succeeded.
`pcm_f32le` @44.1kHz is ~176 KB/s, so 40s is ~7 MB against ~640 KB as MP3.
Stage A transcodes before handing off.

**12. Probe audio duration with ffmpeg. Never trust a supplied duration.**
*`stages/media.py:probe_duration_seconds`, `common/budget.py:video_cost_usd`*
No TTS provider returns duration. The avatar model bills *per output second*,
so the probe sits in the charge path. Fail **closed** to the cap, never to
zero — and note `kling-avatar-v2`'s `fallback_duration` is **5 seconds**, so a
missed probe bills 5s for a 35s video and no cap notices.

**13. Neither gateway supports an idempotency key on submit.**
*`stages/base.py` (partial unique index on in-flight runs), `common/db.py:enqueue_stage_run`*
Neither gateway has one — the field does not exist. A network-level retry of a
submit creates a second provider job and a second charge. Dedupe *before* the
HTTP call; never blind-retry a submit that may have landed. Reconcile instead.

**14. Check the body, not the HTTP status.**
*`stages/vendors.py`, `stages/real.py:webhook_accepted`, pinned by [`tests/test_deliver_webhook.py`](tests/test_deliver_webhook.py)*
Both gateways return HTTP 200 with `code != 200` on error. Also: the video
gateway's `resultJson` is a JSON *string* — parse before indexing, then take
`resultUrls[0]`. On the image gateway the result is nested and `url` may be a
LIST rather than a string: `result.images[0].url`, and its sibling VIDEO
endpoint spells the same shape `result.videos[0].url[0]` — which is invariant 19
in miniature, two endpoints on one gateway disagreeing.

The client's delivery webhook is the same shape and the stakes are higher: it
answers 200 and puts the verdict in `success`, which arrives as the STRING
`"true"`. There is **no failure channel** back to the client, so a delivery
recorded from the status code alone is a video the mechanic never gets and
nobody ever looks for. `webhook_accepted()` fails CLOSED on anything it cannot
read — the cost of being wrong that way is a retry POSTing an identical
`{phone, videoLink}`, which the client stores idempotently.

**15. Copy provider result URLs to our storage immediately.**
*`stages/vendors.py:download`, called in each stage's `poll()`*
Treat a provider URL as valid for the duration of the handler and no longer.
Mirror constraint on the input side: `presigned_get_url` signs for **6 hours**
(`expires_in=21600`, the default and the only value any caller uses), so a job
that sits queued longer than that submits a dead URL. Presign at submit time,
not at enqueue time. Six and not one because the video step can queue for 20
minutes and then take 20 more, and a link that dies mid-render fails the stage
for a reason no log explains.

**16. Hand providers a URL that returns bytes on the first GET.**
*`common/s3.py:presigned_get_url`, `stages/media.py:normalise_for_image_provider`*
Public or presigned, from a source path — never a CDN transform path, which
202s on a cold-cache miss and the provider's fetcher bails. Images must be
within [300, 6000] px on **both** axes; normalise to a *sibling* key, never
overwrite the original.

**17. Never feed a generated image back in as an identity reference.**
*`stages/real.py:ImageStage` — always re-reads `source_photo`*
It compounds its own drift. Always re-reference the source photo. Cap
references at ~4.

**18. Content safety is the dominant image failure** — 11 of 20 observed on
this exact model. Swapping a real person into a branded plate is precisely the
trigger. Needs a softened-prompt retry path and a visible terminal state.
*`stages/vendors.py:image_poll` raises `VendorRejected`, which is not retryable — the same inputs trip the same filter*

**19. Log which provider was tried and why it lost.**
*`common/events.py`, `job_events`*
A fallback chain that swallows the reason is a cost leak nobody can see: a
dead vendor lane 422'd for *months*, was classified retryable, silently fell
through to a lane costing 3×, and left no trace in the database. Validate
against the exact endpoint's schema — sibling endpoints on the same gateway
accept different fields.

**20. A key carries its `S3_PREFIX` from the moment it is built.**
*`common/s3.py`, pinned by `tests/test_storage_keys.py`*
Nothing downstream adds or strips one. The CloudFront distribution has NO
Origin Path, so the full key including `castrol/` must appear in the URL — a
doubled or missing prefix is a 403 that reads exactly like a permissions
failure. `assets.s3_key` is the same string you can paste into `aws s3 cp`.

**21. Presign against the bucket's own regional endpoint.**
*`common/s3.py:S3Backend.__init__`*
boto3's default resolves the global host `<bucket>.s3.amazonaws.com`, and a
SigV4 signature made against that does not validate for a bucket in another
region: the presigned URL 403s while the SDK's own calls succeed. Pin both
`region_name` and `endpoint_url`. Signatures are also METHOD-bound — a HEAD
against a URL signed for GET is a correct 403, not a broken URL.

**22. Delivered links are CDN URLs, never presigned.**
*`stages/real.py:PublishStage`, `common/s3.py:cdn_url`, `infra/s3-lifecycle.json`*
SigV4 caps expiry at 7 days; the client link must live 6 months. A delivered
link dies because the 180-day lifecycle rule DELETES the object. Publishing
copies rather than moves: `jobs/` artefacts never expire, because expiring the
client's link must not destroy the evidence.

**23. Both paid remote stages are async — submit and release.**
*`stages/real.py`, `orchestrator.execute_one`*
Not only for throughput. A submitted run sits in `running`, and the stuck-claim
reaper only touches `claimed`. A synchronous paid stage that outlived
`STAGE_CLAIM_TIMEOUT_S` would be reaped and re-run while the first call was
still in flight, and billed twice.

**24. Record cost at SUBMIT, per attempt — and record the refund too.**
*`common/db.py:mark_running` / `mark_failed`, `0004_runtime_observability.sql`, `0013_refunded_runs.sql`*
The submit is what reserved the money, and recording only on success hides
exactly the failures worth counting. Cost lives on `stage_runs`, never
aggregated onto the job.

**The REASON this invariant used to give — "a task that never completes still
cost money" — is wrong for this vendor, and must not be restated.** Every
failed vendor job refunds its credits, confirmed against the provider dashboard
2026-09-15, with no exception for a rejection or for a task that timed out on
our side. So `mark_failed` sets `stage_runs.refunded` in the same statement
that makes the run terminal, and only when the run actually reserved something
(a free stage stays false). `job_costs` excludes refunded attempts from
`cost_usd` and reports them as `refunded_usd` — visible rather than silently
dropped. Before 0013 a job that failed twice before succeeding read roughly
three times its real bill, in the expensive-looking direction, out of the view
a client quote is built from.

Two consequences worth holding onto. A retry WITHIN one row overwrites the
previous attempt's `cost_usd`, which read as losing a charge and is exactly
right: the earlier attempt was refunded, so the last one is the only figure
ever billed. And `vendor_usage` is deliberately NOT refund-adjusted — its
reservation is what bounds a runaway loop, and a budget that gives money back
on failure is one a retry loop can walk straight through, so the daily caps
still count failed attempts.

**25. Recording an event must never fail a stage.**
*`common/events.py`, pinned by `tests/test_events.py`*
`common/events.py` swallows every write error. The stage above it may have just
spent a dollar; turning a logging outage into a stage failure turns it into a
double charge on the retry. Note the failure handler logs `failed_event=`, not
`event=` — structlog reserves that keyword and the collision raised a
`TypeError` out of the very handler meant to swallow.

**26. Never write a presigned URL or a credential into `job_events`.**
*`common/events.py:_scrub`*
A presigned URL is a bearer credential for one object; the events table is read
by the admin panel and quoted in support threads. `_scrub()` keeps the path and
drops the signature.

**27. The card is rendered by Pillow, after generation, and is free.**
*`stages/media.py:render_card`, geometry pinned by `config.card_template_version`*
No generative model ever touches the text. A card revision is an ffmpeg
re-encode of media we already have — which is why three rounds of client review
on the lower-third cost nothing. `stages/media.py` is the ONE implementation;
`spikes/prototype.py` imports it.

**The rect is FIXED and the type scales to fit — not the other way round.**
A band that grew with its content changed size job to job, and at full width
that reads as a different template rather than as a longer address. The cost is
paid in type size instead, and it is visible: two mechanics in one batch get
different type when one address wraps. A content-driven height was proposed and
rejected on 2026-09-15; it was declined, not overlooked.

**v4 (2026-09-15) slid that same rect down to `y 72.27% .. 87.00%`**, 5.87% of
frame height lower, with nothing else changed. The avatar prompt now parks the
hands at belt height and keeps them there — measured at 60–70% of frame height
across nine renders — which is exactly where the old top edge sat. It cut across
the fingers. The band is now BELOW the hands, so they are visible rather than
half-covered, and invariant 29 no longer gets to assume the card hides them.

`card_template_version` covers the composite OUTPUT, not just the card artwork,
so anything that changes what composite emits bumps it. It is in the composite
`input_hash`, so bumping re-burns every open job — free, local ffmpeg only.

**28. `DELIVERY_ENABLED` gates the only irreversible action.**
*`stages/real.py:DeliverStage`, `config.py:delivery_enabled`*
The client relays the POST to a real mechanic over WhatsApp. While false the
stage logs exactly what it would have sent. Turning it on is a deliberate act.

**29. The avatar `prompt` steers motion — it is not decorative.**
*[`stages/vendors.py`](src/castrol_pipeline/stages/vendors.py) `AVATAR_PROMPT`,
in the hash via `stages/real.py:VideoStage._params`, pinned by
[`tests/test_avatar_prompt.py`](tests/test_avatar_prompt.py)*
On `kling-avatar-v2` the field is required and controls expression, head
movement and hand gesture. The inherited default was literally `"."` — correct
lipsync, hands locked at rest for the whole take. Keep it to a few sentences in
the model's documented shape (subject / expression / motion / style
preservation); long, contradictory, or image-contradicting prompts measurably
degrade output.

**The prompt asks for no hand GESTURES — but it does ask for calm, natural
motion.** Three paid revisions each found a new way for this model to render
gesturing hands badly: a looped, smeared gesture; both hands clawed across the
chest panel; clean open palms that still rose to chest level. `calm` is
load-bearing and is not a synonym for `slow`: slow bounds per-frame
displacement, which is what stopped r1's smear, while calm bounds intent. Frozen
hands are their own defect — a still photograph with a talking head pasted on.

It also asks for the hands to stay **apart and clear of one another**, which the
client asked for by name after reviewing a batch. Hands that meet are where this
model renders fingers worst: it has to invent an occlusion. Measured on the nine
renders of 2026-09-14, seven of nine held them apart for the whole take; the two
that did not converged at the belt near the end.

**The card no longer hides any of this, and that reasoning is retired.** Until
2026-09-15 the argument was that hands resting at ~71–78% of frame height sat
behind an opaque card across 66–82%, so every hand failure was invisible rather
than merely less likely. It was never true of these renders — measured, the
hands sit at 60–70%, so the card's top edge cut across the fingers and they read
as severed by the panel. The card moved DOWN to 72.27% (invariant 27) and the
hands are now on screen for the whole take. The prompt is the only thing keeping
them presentable.

**The HEAD is now bounded too — r5, 2026-09-16, at the client's request.**
r4 asked for `subtle head nods`, and a nod is a repeating movement: this model
performs a requested movement for the whole take rather than occasionally, so
what shipped was a mechanic bobbing continuously for 25 seconds. That is the
same mechanism that looped r1's hand gesture, pointed at the head. The head is
now given a rest position and a bound — level, facing camera, `only slight
natural movement` — which is the shape that already worked for the hands: name
the position positively, then limit the motion rather than forbidding it.

It is bounded and NOT frozen, for the same reason the hands are not: a
motionless head over a moving mouth is a photograph with a talking head pasted
on, which is what the inherited `"."` produced. And with the hands low and the
head steady, the eyes and mouth are the only life left in the frame, so `a
warm, engaged face` replaces the nods — asking for the expression directly
rather than getting it as a side effect of movement.

Three rules survive from the revisions that bought them: never pair a placement
with an exclusion naming the same region (r2's "at chest height ... clear of the
chest logo" is how a hand ended up on the logo), never ask for individuated
fingers (r2's "counting gesture" is where the claw came from), and never name a
repeatable movement — of the hands or the head — unless you want it on a loop.

**30. The avatar prompt is hashed as TEXT, not as a version string.**
*`stages/real.py:VideoStage._params`*
A version string is a thing you can forget to bump — edit the wording, leave
the version, and every existing job skips regeneration and ships the old
motion. Hashing the text removes the failure mode. The price is real: editing
`AVATAR_PROMPT` re-runs the video stage on every job that has not completed, at
$0.036 per output second on standard. Completed jobs are never rescheduled.

**31. A plate is never edited in place - and the uniform reference rides on the
plate row for the same reason.**
*`seed.py:register_plate`, `0009_plate_uniform_reference.sql`*
Replacing a combination's artwork RETIRES the active row and inserts a new one.
Updating in place looks harmless and silently rewrites history: `jobs.plate_id`
keeps pointing at the same row, so every job built from the old artwork starts
claiming it used the new. There is no per-job plate asset to fall back on, so
that link is the only record of what a video was actually made from — and the
client intends to revise this artwork.

`uniform_ref_key` therefore lives on the plate row, not in a mutable table keyed
on `uniform_id`: revising the reference would otherwise rewrite what every
shipped job claims it was built from. Re-registering does NOT inherit the
previous row's reference — omitting `--uniform-ref` means a plate without one,
so the absence of a flag cannot mean two different things depending on history.

**32. A suppressed delivery still SUCCEEDS, so the backlog needs reopening.**
*`cycle.py:_reopen_suppressed_deliveries`*
It has to succeed — the video is made and published, and failing the stage
would park a finished job as `failed` forever. So every video made while
`DELIVERY_ENABLED` was false is already marked delivered: the job is
`completed`, nothing schedules it, and `deliver_hash` covers the CDN url and
the phone, neither of which changes when the flag flips. Switching delivery on
would silently strand the entire backlog, and the failure looks like nothing at
all. Each cycle reopens those jobs once delivery is enabled — the stage is free
and the client stores `{phone, videoLink}` idempotently (invariant 14), so a
repeat post is harmless where a missed one is a video nobody ever gets.

**33. Intake writes one mechanic across THREE transactions, so a crash leaves
a half-created row that no error anywhere reports.**
*`cycle.py:_repair_orphans`*
Each db helper opens its own transaction — the submission, then the job, then
the photo asset — so a process killed between them (deadline, deploy, instance
reboot) leaves either a valid submission with no job, or a job with no source
photo. Neither state is an error: no stage fails, no row is marked, the batch
counters look right, and the mechanic simply never gets a video. The only way
it surfaces is somebody asking why, which is exactly why it is code and not a
runbook note — and continuous deploy makes the interruption routine.

Both halves are free and idempotent (`ON CONFLICT (submission_id)` and
`ON CONFLICT (s3_key)` against the existing constraints), so every cycle
repairs rather than waiting to be noticed, and every repair records a
`job_event` at warning level — a silent self-heal is how a recurring crash
stays invisible for a month. The photo is refetched from `image_url_raw`
byte-exact (invariant 1); a SAS url that has since expired cannot be recovered
and is logged as `cycle.orphan_photo_failed` and skipped, because failing the
whole cycle over one unreachable blob helps nobody.

Known gaps, both open: no test covers it, and `photos_restored` increments on
an insert that `ON CONFLICT` may have skipped.

**34. A terminal failure stays failed at the same inputs.**
*`orchestrator.failure_holds`, called by `schedule_ready`, pinned by [`tests/test_job_run.py`](tests/test_job_run.py)*
The in-flight unique index only covers `pending/claimed/running`, so until
2026-09-16 a `failed` row did not stop `schedule_ready` inserting a fresh
`pending` one at `attempts = 0` — and `execute_one` calls `schedule_ready` in
its `finally`, right after marking the failure. `retry_delay_for` returning None
meant nothing: a content-safety rejection would resubmit the same inputs inside
one `drain_stage` until its limit, and `BUDGET_EXHAUSTED` would hot-loop against
the cap. It never fired only because no run had yet failed terminally.

A failure now stops holding in exactly two cases: the stage's `input_hash`
changed, or it was `BUDGET_EXHAUSTED` on an earlier **IST** day (the cap day).
Anything else is a person's decision — `castrol run <ref> --retry`.
`advance_job` judges by the LATEST run per stage for the same reason, so a job
being retried reads `running`, not `failed`.

---

## Conventions

- Python 3.12+, `uv`, `src/` layout. Matches `behooked_studio_backend`.
- `structlog` JSON to stdout ([`common/logging.py`](src/castrol_pipeline/common/logging.py));
  `job_id` bound inside any job context. Anything worth reconstructing an
  incident from also goes to `job_events` via
  [`common/events.py`](src/castrol_pipeline/common/events.py).
- Stages implement the `Stage` protocol in
  [`stages/base.py`](src/castrol_pipeline/stages/base.py) and are implemented in
  [`stages/real.py`](src/castrol_pipeline/stages/real.py). Stages do not write to
  `jobs` and do not decide retries —
  [`orchestrator.py`](src/castrol_pipeline/orchestrator.py) does both.
- `spikes/` is throwaway and `src/` never imports it. The one exception runs the
  other way: [`spikes/prototype.py`](spikes/prototype.py) imports
  [`stages/media.py`](src/castrol_pipeline/stages/media.py), so the card has a
  single implementation rather than two that drift.
- **Providers are named by ROLE, not by vendor** — the image provider, the
  video provider, the voice provider. Renamed throughout in `14cfc2e`
  (2026-09-15), so `cartesia_tts` → `voice_tts`, `apimart_submit/_poll` →
  `image_submit/_poll`, `kie_submit/_poll` → `video_submit/_poll`,
  `KIE_PROMPT_MAX_CHARS` → `VIDEO_PROMPT_MAX_CHARS`, `normalise_for_apimart` →
  `normalise_for_image_provider`. In [`config.py`](src/castrol_pipeline/config.py)
  the FIELD is the role and `validation_alias` carries the env var name.

  Five places a vendor name still belongs, and they are not oversights:

  | where | why |
  |---|---|
  | env var names (`KIE_API_KEY`, `APIMART_API_KEY`, `CARTESIA_API_KEY`, `CARTESIA_VERSION`) | a deployment contract — the `.env` on the worker and in CI would break |
  | hosts, endpoints and the `Cartesia-Version` header | the actual HTTP call has to reach a real vendor |
  | `kie_video` / `apimart_image` in `vendor_limits` | live primary keys. Renaming needs a migration and a coordinated deploy, so the budget constants and the docs quoting them stay accurate rather than tidy |
  | `supabase/migrations/**` | applied and immutable; `apply_migration.py` keys the ledger on the FILENAME, so `0003_cartesia_tts_no_repair.sql` cannot be renamed without re-applying it |
  | [`docs/CARTESIA_API_DOCS.md`](docs/CARTESIA_API_DOCS.md), [`docs/TALKING_HEAD_PIPELINE_REFERENCE.md`](docs/TALKING_HEAD_PIPELINE_REFERENCE.md) | upstream API reference and another stack's measurements — quoted material, not our prose |

  `require()` reports the **alias**, not the field name, for exactly this
  reason: building the variable name from a renamed field would have told
  whoever hit a missing key to set `VIDEO_API_KEY`, which does not exist.

  Note this is naming, not secrecy — a role name in prose and a real host in a
  curl command are both correct. Nothing in `_params()` or any `input_hash`
  carries a vendor name, which is why the rename re-rendered nothing.
- RLS is deny-all with no policies.
- **Supabase keys are the new style only** — `sb_secret_…` / `sb_publishable_…`,
  never the legacy `anon` / `service_role` JWTs. Those are deprecated by end of
  2026 and were never issued to this project (created after 01 Nov 2025), so if
  something asks for a `service_role` key, that code is wrong. The `service_role`
  *Postgres role* is a different thing and is still what a secret key authorizes
  as — the RLS comments in the migrations are correct as written.
- The pipeline uses **no Supabase API key at all**; it connects to Postgres
  directly via `SUPABASE_DB_URL`. Keys are an admin-panel concern only, and the
  secret key stays server-side.

---

## Current phase

**It is deployed.** `i-0d7560cd333c94cde`, `t3.medium` in `ap-south-1`, running
the CI-built container under a systemd timer — the image digest on the box
matches the one CI pushed, and `castrol doctor` passes through the real `.env`
mount against the real database. **The timer is not armed yet and nothing has
spent.** Resource ids, every decision taken while provisioning, and the
verification evidence are in
[`docs/EC2_DEPLOYMENT.md`](docs/EC2_DEPLOYMENT.md); the runbook is
[`deploy/README.md`](deploy/README.md).

The worker tracks `:latest` with `--pull always`, so a green merge to `main` is
a deploy. The review gate that removes is real — see the Docker / CI section.

**The pipeline runs end to end under the orchestrator** against real Supabase,
real S3 and the real CDN. All eight stages are implemented in
`stages/real.py`; `USE_STUB_STAGES=true` still swaps in deterministic fakes to
exercise the DAG without spending. Migrations 0001–0014 are applied.

Verified on a real job: seed → prep → composite → checks → publish → deliver,
with the delivered CDN URL returning 200. The three paid stages are the same
calls the prototype proved, now under budget reservation and cost recording.

**Intake is written against the real CSV export**, confirmed against a live
pull on 2026-09-08 and the client's combination map on 2026-09-09. Six defects
were fixed together, because each one alone rejected every row:

- `intake/export_client.py` parses CSV, decoding **`utf-8-sig`**. The body
  carries a UTF-8 BOM; as plain utf-8 the first header becomes `﻿id` and
  `id` — the only unique identifier in the feed — silently reads as missing
  while the other 17 columns parse perfectly.
- The two phone columns are separated. `whatsapp_number` → `phone_e164`, the
  delivery key and the dedupe anchor. `mechanic_phone_number` →
  `card_phone_e164`, printed and nothing else.
- `prep/plates.py` maps what the export actually sends. Background 1|2|3 **are**
  the SUV, sedan and hatchback, so the ids were always right — the lookup keys
  were not. The client's own words (SUV / Sedan / Hatchback, Uniform 1 / 2) are
  accepted as aliases.
- Uniform ids are `u1_tshirt` / `u2_uniform`, the client's number and the
  client's word. `polo` / `half_shirt` were ours, and one of the two values is
  literally a t-shirt — which way round they mapped was a coin flip.
- `EXPORT_TIMESTAMP_FORMAT` is `iso8601`. Still pinned, still never inferred;
  it names the standard instead of restating its pattern.
- **There is no `image_face_count`.** Rekognition arrives as a status string,
  which says a face was found but not how many — so the group-photo case is no
  longer detectable at intake and falls to the stage B checks.
  `tests/test_intake.py` asserts that gap deliberately.

Real header, in order:

```
id, whatsapp_number, user_name, workshop_name, address, gender,
mechanic_id_verified, mechanic_id, mechanic_phone_number, background,
outfit, image_url, image_mime_type, image_validation_status,
image_rekognition_status, status, createdAt, updatedAt
```

Client-confirmed guarantees, all encoded as CHECKS rather than assumptions —
if one stops holding we get a row with a stable reject code, not a broken
video: `mechanic_phone_number` is never empty, `whatsapp_number` is unique,
`address` is never empty, `background` never holds a seventh value.

**The address is free text, any shape** (client, 2026-09-09). It used to be
required to be exactly `Locality, City`, which rejected real people for writing
their own address normally — a one-word `Worli` was a `BAD_ADDRESS`. The card
prints the whole thing; the voice says only the last segment, because Indian
addresses run most-specific to least and reading it all aloud puts a hospital
landmark in a 30-second ad. `MAX_ADDRESS_CHARS` is now a sanity bound (90), not
a layout rule.

`address_normalized` holds the **spoken** form, not a tidied postal address.
`address_raw` is what the card prints.

**The image edit takes a third input: a plain-background shot of the uniform.**
Added 2026-09-10, migration `0009`. Stage B was asking the model to keep the
plate's garment while redrawing the body inside it, so fabric, seams and the
printed marks were reconstructed rather than copied — and the chest logo is what
the video is for. `plates.uniform_ref_key` is nullable, and **all six active
rows now carry one** — `uniform/u1_tshirt.png` on plates 01–03 and
`uniform/u2_uniform.png` on 04–06. A plate without one submits the two images it
always did, on the two-image prompt (invariant 6). The reference is in the image
stage's `input_hash`, so jobs that have not run stage B regenerate rather than
skip. Prove a new reference through `spikes/prototype.py --uniform-ref`
(~$0.014) before registering it — the same edit, without the $1 video behind it.

It works. At stage B the chest panel comes back pixel-crisp where the plate
alone produced a smear, and that survives the avatar model too.

**The artwork was REPLACED on 2026-09-11 and re-registered on 2026-09-15** —
`approved_by = "new artwork 2026-09-11 - Castrol-only chest, plain sleeves"`,
all six at **1152x2048**, which is exactly 9:16. The previous set was 1536x2752
(0.5581) and logged `seed.plate_not_1080x1920` six times; the new set still logs
it, still harmlessly, because the warning is about resolution and the card
geometry is expressed as FRACTIONS of the frame.

What changed in the artwork, and why the prompt had to follow: the new uniforms
have **no cap and no sleeve logo, and the chest panel reads `Castrol` alone** —
not `Castrol MAGNATEC` on two lines. `IMAGE_PROMPT`'s preserve clause used to
name all four marks, so it was asking the model to keep branding the garment no
longer has, and the model duly invented a garbled sleeve patch.
That was **v2**. Unlike the avatar prompt (invariant 30) this one is hashed by
VERSION, so it must be bumped by hand or open jobs skip stage B and ship the
old inventory.

**`image_prompt_version` is now v3 (2026-09-16), for naturalness on faces.**
The preserve clause said "carry over their facial hair", and that is what made
the gap easy to miss: naming a feature tells the model the feature is there,
not that its STRUCTURE has to be read off the reference. Given only the noun, a
bearded mechanic came back with a beard-shaped mass — soft at the jawline,
smeared into the lips, its density and grey invented. The CHANGE half now has a
third paragraph asking for the structure by name (outline, jawline edge,
length, density, patchiness, growth direction, grey), for hair resolved as
individual hairs, and for skin with its own texture rather than a plastic
sweep. The CONSTRAIN half blocks the two ways `beautify` shows up on a face —
smoothing the skin, tidying the hair. It ends with "a clean-shaven man stays
clean-shaven", because a paragraph about beards is otherwise an invitation to
add one. Pinned by `tests/test_image_prompt.py`.

A second, unlooked-for win: the single-word chest mark survives the avatar
model's per-frame redraw where the two-line one never did. Nine of nine renders
on 2026-09-14 read a clean `Castrol`; every earlier render smeared
`Castrol MAGNAT..`. Months of that was blamed on hand motion and on tier
resolution. It was the artwork.

Each combination now has three rows, one active and two retired, and older jobs
still point at the artwork they were built from (invariant 31 doing its job).

**To check the repo and the database agree, hash the NORMALISED file, not the
raw one.** `register_plate` stores `sha256(normalise_for_image_provider(file))`, and
that step converts to RGB and re-encodes the PNG — so the sha of
`plates/plate_01.png` on disk NEVER equals `plates.sha256`, even when the
artwork is identical. Comparing raw hashes reports every plate as diverged,
always. The real check:

```python
norm = media.normalise_for_image_provider(path, tmp)     # what actually gets uploaded
hashing.sha256_hex(norm.read_bytes()) == row["sha256"]
```

One consequence worth knowing: `register_plate` logs `seed.plate_not_1080x1920`
six times whatever you feed it, because the check is on RESOLUTION. Expected and
harmless — the card geometry is expressed as FRACTIONS of the frame, so plate
resolution does not move it. A plate at a genuinely different ASPECT would
matter; a different resolution does not. (The 2026-09-11 set is 1152x2048, a
true 9:16; the 2026-09-09 set was 1536x2752, or 0.5581 against 9:16's 0.5625.)

**The plate the model returns is 720x1280 or 1072x1920 regardless**, because
that is the tier's output size, not the plate's — see the cost section.

**Spike 0.1 is answered — do not rewrite the script.** `kling-avatar-v2` has
completed in prod at 39s on our video provider and 60s on another; the ~80-word
script at 30–40s is comfortably inside proven range. The original 18–25s assumption was too
conservative by about half. Budget **8–20 minutes** of wall clock per render,
not two.

**Stage A is the voice provider, direct API** — the one deliberate exception to
"the two gateways only", because that intersection has no voice-cloning Hindi
lane. The voice is created by hand in that provider's dashboard and referenced
by id: **there is no cloning call in the pipeline.**

**There is no repair pass.** A second lipsync pass was considered and dropped
— quality is solved in the main flow. If stage C output is unacceptable the
fix is its inputs, not a patch stage. Do not reintroduce it.

**A reboot resumes itself.** The timer also fires `OnBootSec=5min`, because
`Persistent=true` only covers a run that never started — a cycle killed
mid-batch was not "missed". The docker unit removes a leftover `castrol-cycle`
container first, which a hard stop leaves behind and which would otherwise fail
that very start on a name conflict.

**The run is automated, twice a day.** `castrol cycle` is the whole flow —
pull, schedule, work, wait, stop — driven by a systemd timer at 00:00 and 12:00
IST on EC2 ([`deploy/`](deploy/README.md)). It is not `drain` under a timer:
`drain` stops the moment a sweep moves nothing, which for an async stage means
"still rendering", so under a timer it would submit every paid render and exit
before collecting one. The cycle waits, holds an advisory lock so two runs
cannot overlap, and stops at a deadline (8h, inside the 12h gap) rather than
running into the next window. Nothing is lost when it stops early — readiness
is recomputed from `stage_runs`, so the next cycle resumes.

The pull window is `[today − 1, today + 1]` on the CLIENT's calendar, not the
server's: EC2 runs UTC and 00:00 IST is still yesterday there. Both ends are
loose because re-pulling is free and a missed row is not recoverable —
`intake._already_seen` dedupes on the submission hash AND the client's own row
`id`, the second of which is what stops an overlapping window turning a
re-issued media url into a UNIQUE violation that fails the whole batch.

**The open unknown is geometry drift** — spike 0.3. See below.
