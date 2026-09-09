# CLAUDE.md — Castrol MAGNATEC video pipeline

Batch video factory. Pull mechanic submissions from the client's export API,
generate a personalised vertical promo video each, host it, POST the URL to the
client's delivery webhook.

Read [`docs/TECH_DESIGN.md`](docs/TECH_DESIGN.md) before changing anything
structural. [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) holds scope, client
decisions and risks.

---

## Accounts — read this first

**This project uses a dedicated set of accounts, separate from every other
BeHooked project.** GitHub, AWS, Supabase, Vercel and the AI provider are all
different logins. Never reuse a credential, project ref, bucket or CLI profile
from `BeHooked/Webapp` or `behooked_studio_backend`.

- **Git remote:** SSH alias `github-castrolinfra` (account `castrolinfra-sys`).
  The repo has a local `user.name` / `user.email` set to match — do not run git
  commands that would fall back to the global identity.
- **`gh` CLI is authenticated as `nachimore`, the wrong account.** Do not use
  `gh` for anything that writes to this repo.
- **Supabase / AWS / Vercel / apimart:** credentials live in `.env` only.
  `.env.example` documents every variable.

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
casually — it is ~$0.04 per second of output.

### Pipeline

→ [`cli.py`](src/castrol_pipeline/cli.py) dispatches all of these;
`seed-job` is [`seed.py`](src/castrol_pipeline/seed.py), the rest run through
[`orchestrator.py`](src/castrol_pipeline/orchestrator.py).

```bash
uv run castrol doctor
```

**The whole run, unattended.** This is what the EC2 timer fires at 00:00 and
12:00 IST and the only command the server executes: pull the window, schedule,
then work and wait until every job is terminal or the deadline hits. See
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
uv run castrol seed-job --photo spikes/in/mechanic2.jpg --plate spikes/in/plate_bg2.png --name "Amit Kumar" --workshop "Ganesh Car Service" --address "Beturkar Pada, Opposite New National Hospital, Andheri" --phone 9773128990 --uniform u1_tshirt --background bg2_dark_sedan
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
the metric it reports is DURATION, seconds of video delivered. That line is held
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
| variable | `DOCKERHUB_IMAGE` | full repo, e.g. `acct/castrol-video-pipeline` |

Tags: `latest` and `main-<sha>` on `main`, semver on `v*`. **Deploys pin the
sha tag** — a worker that spends per run should not track a mutable tag.

The image carries no credentials. Supply them at run time:

```bash
docker run --rm --env-file .env acct/castrol-video-pipeline:main-abc1234 work --stage audio
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

apimart reports USD directly (1 apimart credit = $0.10). kie reports its own
credits at roughly 166 per USD — inferred from a refusal, not confirmed.

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
0001–0008 are now applied and registered.

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
| The unattended run: lock, window, wait loop | [`cycle.py`](src/castrol_pipeline/cycle.py) |
| systemd units + the EC2 runbook | [`deploy/`](deploy/README.md) |
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
| Export pull (**CSV, BOM**), validation, dedupe | [`intake/`](src/castrol_pipeline/intake/) |
| Create one job by hand | [`seed.py`](src/castrol_pipeline/seed.py) |
| Every env var and pinned model id | [`config.py`](src/castrol_pipeline/config.py) / [`.env.example`](.env.example) |
| Schema, budget function, RLS | [`supabase/migrations/`](supabase/migrations/) |
| Lifecycle rules, bucket posture | [`infra/`](infra/) |
| The standalone one-video script | [`spikes/prototype.py`](spikes/prototype.py) |
| Admin panel (Next.js, Vercel) | [`panel/`](panel/) |
| The panel's only DB handle — secret key, bypasses RLS | [`panel/lib/db.ts`](panel/lib/db.ts) |
| Who may open the panel | [`panel/middleware.ts`](panel/middleware.ts) |
| The panel's only write | [`panel/app/actions.ts`](panel/app/actions.ts) |
| Duration-only views the panel reads | [`0007`](supabase/migrations/0007_usage_views_for_the_panel.sql), [`0008`](supabase/migrations/0008_job_usage_video_url.sql) |

Full annotated map with per-file descriptions: [`README.md`](README.md#where-things-live)
and [`docs/TECH_DESIGN.md` §3](docs/TECH_DESIGN.md).

---

## Inputs per video

| Input | Source | Used for |
|---|---|---|
| Plate (uniform + background) | frozen, chosen by export `outfit` + `background` | the scene |
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
`submissions.phone_e164` is the delivery key (`whatsapp_number`), and the card
number needs its own column — today `stages/real.py` renders the card from
`phone_e164`, so until that is split the card prints the delivery number.
`mechanic_id` would be the natural primary key but is not reliable enough to
use as one; the export's own `id` is.

---

## Cost

```
cost = $0.014 + seconds x $0.040886        (25s ~ $1.04)
```

The video step is **96.5%** of it and bills per output second, so runtime is
the only lever worth pulling. `ai-avatar-pro` doubles the total. kie ceils to
whole seconds, so 24.8s bills as 25s.

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
*`stages/vendors.py:IMAGE_PROMPT`*
The card sits at a fixed pixel position. If person replacement shifts subject
scale or the belt line, the card lands on the mechanic's hands. Change /
Preserve / Constrain prompt structure is deliberate.

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

The rest come from [`docs/TALKING_HEAD_PIPELINE_REFERENCE.md`](docs/TALKING_HEAD_PIPELINE_REFERENCE.md),
which is prod-measured evidence from the existing BeHooked backend. Each one
below is a failure someone already paid for.

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
Not kie, not apimart — the field does not exist. A network-level retry of a
submit creates a second provider job and a second charge. Dedupe *before* the
HTTP call; never blind-retry a submit that may have landed. Reconcile instead.

**14. Check the body, not the HTTP status.**
*`stages/vendors.py`, `stages/real.py:webhook_accepted`, pinned by [`tests/test_deliver_webhook.py`](tests/test_deliver_webhook.py)*
Both gateways return HTTP 200 with `code != 200` on error. Also: kie's
`resultJson` is a JSON *string* — parse before indexing. apimart's video
result is `result.videos[0].url[0]` — `url` is a list.

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
Mirror constraint on the input side: presigned URLs expire in 1 hour, so a job
that sits queued longer submits a dead URL. Presign at submit time, not at
enqueue time.

**16. Hand providers a URL that returns bytes on the first GET.**
*`common/s3.py:presigned_get_url`, `stages/media.py:normalise_for_apimart`*
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
*`stages/vendors.py:apimart_poll` raises `VendorRejected`, which is not retryable — the same inputs trip the same filter*

**19. Log which provider was tried and why it lost.**
*`common/events.py`, `job_events`*
A fallback chain that swallows the reason is a cost leak nobody can see: a
dead kie lane 422'd for *months*, was classified retryable, silently fell
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

**24. Record cost at SUBMIT, per attempt.**
*`common/db.py:mark_running`, `0004_runtime_observability.sql`*
The submit is what spent the money; a task that never completes still cost
money, so recording only on success hides exactly the failures worth counting.
Cost lives on `stage_runs`, never aggregated onto the job — a job that retried
the video step really did pay twice.

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

**31. A plate is never edited in place.**
*`seed.py:register_plate`*
Replacing a combination's artwork RETIRES the active row and inserts a new one.
Updating in place looks harmless and silently rewrites history: `jobs.plate_id`
keeps pointing at the same row, so every job built from the old artwork starts
claiming it used the new. There is no per-job plate asset to fall back on, so
that link is the only record of what a video was actually made from — and the
client intends to revise this artwork.

**27. The card is rendered by Pillow, after generation, and is free.**
No generative model ever touches the text. A card revision is an ffmpeg
re-encode of media we already have — which is why three rounds of client review
on the lower-third cost nothing. `stages/media.py` is the ONE implementation;
`spikes/prototype.py` imports it.

**29. The avatar `prompt` steers motion — it is not decorative.**
*[`stages/vendors.py`](src/castrol_pipeline/stages/vendors.py) `AVATAR_PROMPT`,
in the hash via `stages/real.py:VideoStage._params`, pinned by
[`tests/test_avatar_prompt.py`](tests/test_avatar_prompt.py)*
On `kling-avatar-v2` the field is required and controls expression, head
movement and hand gesture. The inherited default was literally `"."` — correct
lipsync, hands locked at rest for the whole take. Keep it to a few sentences in
the model's documented shape (subject / expression / motion / style
preservation); long, contradictory, or image-contradicting prompts measurably
degrade output. Two clauses are ours, not the model's: gestures at **chest
height**, because the card is an opaque overlay over 66–82% of frame height
and a waist-level gesture happens behind it; and off the **chest logo**, which is
what the video is for.

**30. The avatar prompt is hashed as TEXT, not as a version string.**
*`stages/real.py:VideoStage._params`*
A version string is a thing you can forget to bump — edit the wording, leave
the version, and every existing job skips regeneration and ships the old
motion. Hashing the text removes the failure mode. The price is real: editing
`AVATAR_PROMPT` re-runs the video stage on every job that has not completed, at
$0.04 per output second. Completed jobs are never rescheduled.

**28. `DELIVERY_ENABLED` gates the only irreversible action.**
*`stages/real.py:DeliverStage`, `config.py:delivery_enabled`*
The client relays the POST to a real mechanic over WhatsApp. While false the
stage logs exactly what it would have sent. Turning it on is a deliberate act.

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

**The pipeline runs end to end under the orchestrator** against real Supabase,
real S3 and the real CDN. All eight stages are implemented in
`stages/real.py`; `USE_STUB_STAGES=true` still swaps in deterministic fakes to
exercise the DAG without spending. Migrations 0001–0008 are applied.

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

**All six plates are live and active.** Uploaded 2026-09-09 from
`plates/plate_0N.png`, content-addressed by sha256. The artwork is provisional
— the client has not finalised it — but nothing is blocked on that: re-running
`register_plate` with replaced files supersedes them.

**Spike 0.1 is answered — do not rewrite the script.** `kling-avatar-v2` has
completed in prod at 39s via kie and 60s via fal; the ~80-word script at 30–40s
is comfortably inside proven range. The original 18–25s assumption was too
conservative by about half. Budget **8–20 minutes** of wall clock per render,
not two.

**Stage A is Cartesia, direct API** — the one deliberate exception to
"apimart + kie only", because that intersection has no voice-cloning Hindi
lane. The voice is created by hand in the Cartesia dashboard and referenced by
id: **there is no cloning call in the pipeline.**

**There is no repair pass.** A second lipsync pass was considered and dropped
— quality is solved in the main flow. If stage C output is unacceptable the
fix is its inputs, not a patch stage. Do not reintroduce it.

**The run is automated, twice a day.** `castrol cycle` is the whole flow —
pull, schedule, work, wait, stop — driven by a systemd timer at 00:00 and 12:00
IST on EC2 ([`deploy/`](deploy/README.md)). It is not `drain` under a timer:
`drain` stops the moment a sweep moves nothing, which for an async stage means
"still rendering", so under a timer it would submit every paid render and exit
before collecting one. The cycle waits, holds an advisory lock so two runs
cannot overlap, and stops at a deadline (4h, inside the 12h gap) rather than
running into the next window. Nothing is lost when it stops early — readiness
is recomputed from `stage_runs`, so the next cycle resumes.

The pull window is `[today − 1, today + 1]` on the CLIENT's calendar, not the
server's: EC2 runs UTC and 00:00 IST is still yesterday there. Both ends are
loose because re-pulling is free and a missed row is not recoverable —
`intake._already_seen` dedupes on the submission hash AND the client's own row
`id`, the second of which is what stops an overlapping window turning a
re-issued media url into a UNIQUE violation that fails the whole batch.

**The open unknown is geometry drift** — spike 0.3. See below.
