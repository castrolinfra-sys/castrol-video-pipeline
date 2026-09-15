# Castrol MAGNATEC — mechanic promo video pipeline

A batch video factory. For each mechanic submission pulled from the client's
export API it generates a vertical promo video — a fixed Hindi/Hinglish script
with the mechanic's name, workshop and location spoken in a fixed voice, over
one of six pre-built garage scenes, with a personalisation card burned in —
hosts it, and POSTs the URL back to the client's delivery webhook.

WhatsApp is not in scope. The client runs that on Interakt; we see two HTTP
contracts and nothing else.

---

## Inputs per video

| # | Input | Where it comes from | Used for |
|---|---|---|---|
| 1 | **Plate** — uniform + garage background | pre-built, frozen, human-approved. Chosen by the export's `outfit` + `background` | the scene the mechanic is composited into |
| 2 | **Mechanic photo** | export `image_url` (Azure blob) | the face/build swapped onto the plate |
| 2b | **Uniform reference** — the garment alone on a plain background | pre-built, frozen, registered on the plate row. Nullable, and **all six active plates carry one** | a third reference to the image edit, so the uniform's fabric and printed marks are copied rather than reconstructed |
| 3 | **Name** | export `user_name` | **spoken** and on the card |
| 4 | **Workshop name** | export `workshop_name` | **spoken** and on the card |
| 5 | **Location** | export `address` — free text, any shape | card prints it whole; the voice says only its **last segment** |
| 6 | **Delivery phone** | export `whatsapp_number` | **delivery key only** — what we POST back as `phone`. Never printed, never spoken |
| 7 | **Card phone** | export `mechanic_phone_number` | **card only** — never spoken |

Only 3–5 vary inside the script; the rest of the script is identical for every
mechanic. Neither phone number is ever read aloud.

**6 and 7 are two different numbers doing two different jobs**, confirmed by the
client 2026-09-08: `submissions.phone_e164` is the delivery key the client
relays on, `card_phone_e164` is the contact number in the card's green panel.
They are not interchangeable. The renderer printed the wrong one until
2026-09-15 — the column existed and intake populated it correctly, so nothing
looked broken while every card carried a mechanic's WhatsApp number burned into
a video that gets shared around.

---

## The pipeline

```
      plate + mechanic photo
              │
      prep    fill the script, pick the spoken locality        free
        ├───────────────┐
  [A] audio        [B] image                                   $0.02 / $0.014
  voice provider   image provider, gpt-image-2 @2K
  sonic-3.6        person replacement on the plate
        └───────┬───────┘
  [C] video    kling-avatar-standard       (8-20 MINUTES)     $0.036 / second
        │
  [D] composite  Pillow + ffmpeg, burn in the lower-third      free
        │
    checks     duration / aspect / bitrate, non-blocking       free
        │
    publish    copy to deliver/<uuid4>/ + CDN URL              free
        │
    deliver    POST {phone, videoLink}   (OFF by default)
```

A and B are independent and run ahead. C is the bottleneck and 96% of the cost.

All eight stages live in
[`stages/real.py`](src/castrol_pipeline/stages/real.py) as one class each. The
work they share is split by kind, not by stage:
[`vendors.py`](src/castrol_pipeline/stages/vendors.py) is everything that talks
to a provider, [`media.py`](src/castrol_pipeline/stages/media.py) is everything
local and free.

| Stage | Class | Provider call | Local work | Cost |
|---|---|---|---|---|
| `prep` | `PrepStage` | — | [`prep/script.py`](src/castrol_pipeline/prep/script.py), [`prep/normalise.py`](src/castrol_pipeline/prep/normalise.py) | free |
| `audio` **A** | `AudioStage` | [`vendors.py`](src/castrol_pipeline/stages/vendors.py) `voice_tts` | [`media.py`](src/castrol_pipeline/stages/media.py) `to_mp3`, `probe_duration_seconds` | $0.00005/char |
| `image` **B** | `ImageStage` | [`vendors.py`](src/castrol_pipeline/stages/vendors.py) `image_submit` / `image_poll` | — | $0.014 |
| `video` **C** | `VideoStage` | [`vendors.py`](src/castrol_pipeline/stages/vendors.py) `video_submit` / `video_poll` | — | $0.036/s |
| `composite` **D** | `CompositeStage` | — | [`media.py`](src/castrol_pipeline/stages/media.py) `render_card`, `composite` | free |
| `checks` | `ChecksStage` | — | — | free |
| `publish` | `PublishStage` | — | [`common/s3.py`](src/castrol_pipeline/common/s3.py) `copy`, `cdn_url` | free |
| `deliver` | `DeliverStage` | client webhook | — | free |

The DAG, the `Stage` protocol and `StageResult` are in
[`stages/base.py`](src/castrol_pipeline/stages/base.py). Claiming, retries, the
poller and every write to `jobs` are in
[`orchestrator.py`](src/castrol_pipeline/orchestrator.py) — stages do neither.
[`stages/stubs.py`](src/castrol_pipeline/stages/stubs.py) holds no-spend doubles
for the same protocol (`USE_STUB_STAGES=true`).

Every stage is claimed with `FOR UPDATE SKIP LOCKED`, keyed by an `input_hash`
covering model ids and prompt/template versions, and recorded in `stage_runs`.
So a crash at video does not repay for audio and image, and changing a model id
regenerates instead of skipping.

**B and C submit and release**; a separate poller reconciles. That is not only
throughput: a submitted run sits in `running`, and the stuck-claim reaper only
touches `claimed`. A synchronous paid stage that outlived
`STAGE_CLAIM_TIMEOUT_S` would be reaped and re-run while the first call was
still in flight, and billed twice.

**Card work is free.** The card is rendered deterministically by Pillow after
generation, so a revision to the lower-third is an ffmpeg re-encode of media we
already have — not a regenerated second.

**The avatar prompt drives motion, not just lipsync.** On `kling-avatar-v2` the
`prompt` field steers expression, head movement and hand gesture; the inherited
default was literally `"."`, which produced correct lipsync with the hands
locked at rest. `AVATAR_PROMPT` in
[`stages/vendors.py`](src/castrol_pipeline/stages/vendors.py) deliberately asks
for **no hand gestures**: hands stay low, one on each side, apart and clear of
one another, moving only with calm, slow, natural motion. Three paid revisions
each found a new way for this model to render moving hands badly, and "apart and
clear of one another" is the client's own phrasing after a batch review — hands
that meet are where this model renders fingers worst, because it has to invent
an occlusion.

The card used to hide the hands and no longer does. The old argument — an opaque
overlay across 66-82% of frame height against hands resting at ~71-78% — did not
survive measurement: the hands sit at 60-70%, so that top edge cut across the
fingers. The card moved down to 72.27% (template v4), the hands are on screen
throughout, and the prompt is the only thing keeping them presentable. The face
still carries the video.

The prompt text is part of the video `input_hash`, not a version string you can
forget to bump. Editing it therefore regenerates — at $0.036 per output second
on standard, for every job that has not completed.

### Storage and URLs

Two kinds of read, and the difference is not cosmetic:

| | For | Lifetime |
|---|---|---|
| **Presigned GET** | handing a working artefact to a vendor — the gateways fetch inputs by URL | 6h, method-bound, private |
| **CDN URL** | the delivered video only | until the object is **deleted** at 180 days |

Delivered links are never presigned: SigV4 caps expiry at 7 days and the client
link must live 6 months. A link dies because the lifecycle rule removes the
object, not because a signature lapsed.

Keys carry the `S3_PREFIX` from the moment they are built and nothing
downstream adds one — the distribution has no Origin Path, so a doubled or
missing `castrol/` is a 403 that reads exactly like a permissions failure.

Publishing **copies** rather than moves. The delivered object expires at 180
days; the working artefacts under `jobs/` never do. Expiring the client's link
must not destroy the evidence of how the video was made.

---

## Setup

```bash
uv sync
```

```bash
cp .env.example .env
```

Fill [`.env`](.env.example) — every variable is documented there. Then apply
[`supabase/migrations/`](supabase/migrations/) in order, against this project's
own Supabase instance, with
[`scripts/apply_migration.py`](scripts/apply_migration.py).

> GitHub, Supabase, Vercel and the three AI providers are **dedicated
> accounts**, separate from other BeHooked projects. **AWS is not** —
> `castrol-local` lives in the shared BeHooked account `872515254882`; only the
> IAM user and the bucket are dedicated. See [`CLAUDE.md`](CLAUDE.md).

---

## Commands

### Prototype — one video, end to end

→ [`spikes/prototype.py`](spikes/prototype.py). The Phase 0 path. No database, no orchestrator; it does by hand what the
pipeline will later do properly. Steps are resumable, so a crash during the
20-minute video step does not repay for the image and audio.

Full run:

```bash
uv run python spikes/prototype.py --plate spikes/in/plate.png --photo spikes/in/mechanic.jpg --script spikes/in/script_spoken.txt --out spikes/out/run1 --name "Raju Shetty" --workshop "Shetty Motors" --address "Andheri, Mumbai" --phone "9898989898"
```

One step at a time — `image`, `audio`, `video`, `composite`:

```bash
uv run python spikes/prototype.py --out spikes/out/run1 --only audio --script spikes/in/script_spoken.txt
```

Skip the plate and drive the avatar straight from a photo (useful before the
plates exist):

```bash
uv run python spikes/prototype.py --out spikes/out/run1 --photo spikes/in/mechanic.jpg --script spikes/in/script_spoken.txt --only video
```

Re-run a step that already succeeded by deleting its key from
`spikes/out/<run>/_state.json`.

### Pipeline

→ [`cli.py`](src/castrol_pipeline/cli.py) for every command below; `seed-job` is [`seed.py`](src/castrol_pipeline/seed.py) and the rest run through [`orchestrator.py`](src/castrol_pipeline/orchestrator.py).

Check config and the database are actually usable before anything else:

```bash
uv run castrol doctor
```

**The whole run, unattended.** This is the only command the EC2 timer executes,
at 00:00 and 12:00 IST: take an advisory lock, pull the client's window, finish
any intake rows a crash left half-created, reopen deliveries suppressed while
`DELIVERY_ENABLED` was false, schedule every unfinished job, then work and wait
until nothing is queued and nothing is in flight — or the 8-hour deadline hits,
which loses nothing because readiness is recomputed from `stage_runs`. The lock
means a noon run that lands on a still-rendering midnight run exits rather than
doubling the concurrent load on a paid vendor. **It spends money.** See
[`cycle.py`](src/castrol_pipeline/cycle.py) and
[`deploy/README.md`](deploy/README.md).

```bash
uv run castrol cycle
```

Finish what is already in the database without pulling anything new:

```bash
uv run castrol cycle --no-fetch
```

**One video by hand.** The manual-entry path — a single mechanic, a plate test,
a client sample — without the export API in the way. Creates the rows and lands
the photo in S3; runs nothing. Idempotent on (photo, phone).

```bash
uv run castrol seed-job --photo spikes/in/mechanic2.jpg --plate spikes/in/plate_bg2.png --uniform-ref uniform/u1_tshirt.png --name "Amit Kumar" --workshop "Ganesh Car Service" --address "Beturkar Pada, Opposite New National Hospital, Andheri" --phone 9773128990 --uniform u1_tshirt --background bg2_dark_sedan
```

**Plate artwork on its own**, with the uniform reference the image edit uses as
its third input. Append-only: the combination's current row is retired and a new
one inserted, so every existing job keeps naming the artwork it was really built
from. Free, and runs nothing.

```bash
uv run castrol register-plate --plate plates/plate_02.png --uniform u1_tshirt --background bg2_dark_sedan --uniform-ref uniform/u1_tshirt.png
```

The reference belongs to the uniform rather than the background, so the same
file is normally registered against all three of that uniform's combinations —
it is content-addressed in S3, so that stores one object. Omit `--uniform-ref`
and the combination submits plate + photo only, on the two-image prompt.

`--address` is what the **card** prints. What the voice **says** defaults to the
last segment of it (`Andheri`), so landmarks are not read aloud; override with
`--spoken-place`.

**Run it by hand.** `drain` sweeps every stage and the poller until nothing
moves — convenient locally. It is NOT what production runs: `drain` stops the
moment a sweep moves nothing, which for an async stage means "still rendering",
so under a timer it would submit every paid render and exit before collecting
one. That is what `cycle` is for.

```bash
uv run castrol drain
```

```bash
uv run castrol work --stage audio
```

One pass of the poller, or block until the queue is empty — which is what you
want while a 20-minute avatar render is in flight. Ctrl-C is safe; the task
keeps running at the vendor and the next poll picks it up.

```bash
uv run castrol poll --watch
```

**Re-run a stage on a finished job.** For when an input the hash cannot see has
changed — a reworded avatar prompt, a corrected model id, a plate reissued
under the same key. It demotes the succeeded runs for that stage and everything
after it, reopens the job and re-schedules. **It spends money on the next
`work`.**

```bash
uv run castrol redo <job-id> --stage video
```

Succeeded runs are marked `skipped`, never deleted — the row carries what that
attempt cost, and a deleted row takes that with it.

**Bulk intake** from the client export, for one window, without the rest of a
cycle. Written against the real CSV export and confirmed against a live pull;
dedupes on the submission hash and on the client's own row `id`, so an
overlapping window is free:

```bash
uv run castrol intake --from 2026-09-01 --to 2026-09-01
```

Recover a batch whose scheduling was interrupted:

```bash
uv run castrol schedule
```

### Inspecting a run

→ [`cli.py`](src/castrol_pipeline/cli.py), reading `job_costs` and `job_events` from [`0004`](supabase/migrations/0004_runtime_observability.sql).

Everything known about one job — stage runs, cost, assets, checks:

```bash
uv run castrol show <job-id>
```

The durable timeline, oldest first:

```bash
uv run castrol events <job-id>
```

Per-generation spend, and today's usage against the caps:

```bash
uv run castrol costs
```

The morning number for a batch:

```bash
uv run castrol report
```

### Migrations

→ [`scripts/apply_migration.py`](scripts/apply_migration.py), against [`supabase/migrations/`](supabase/migrations/). Forward-only, numbered, one file per invocation, each in a single transaction.

```bash
uv run python scripts/apply_migration.py supabase/migrations/0004_runtime_observability.sql
```

### Checks

```bash
uv run pytest
```

```bash
uv run ruff check .
```

```bash
uv run ruff format .
```

### Client export API

```bash
curl -sS "https://capi.letschbang.com/api/submissions/export/vendor?from=2026-09-02&to=2026-09-03" -H "apikey: $CLIENT_EXPORT_API_KEY"
```

Returns **CSV**, not JSON. Rate limit 100 requests / 900s.

### Balances

```bash
curl -sS https://api.apimart.ai/v1/user/balance -H "Authorization: Bearer $APIMART_API_KEY"
```

```bash
curl -sS https://api.kie.ai/api/v1/chat/credit -H "Authorization: Bearer $KIE_API_KEY"
```

### AWS

One-time administrative setup, run with an admin account — see
[`infra/README.md`](infra/README.md). The pipeline's IAM user deliberately
cannot change bucket configuration.

---

## Where things live

Every path is real. If you are hunting for where something happens, start here.

### Entry points

| File | Handles |
|---|---|
| [`src/castrol_pipeline/cli.py`](src/castrol_pipeline/cli.py) | every `castrol <cmd>` — `doctor`, `seed-job`, `register-plate`, `drain`, `work`, `poll`, `schedule`, `intake`, `show`, `events`, `costs`, `report` |
| [`src/castrol_pipeline/orchestrator.py`](src/castrol_pipeline/orchestrator.py) | readiness, claiming, retries, the poller, and **every write to `jobs`** |
| [`src/castrol_pipeline/seed.py`](src/castrol_pipeline/seed.py) | `castrol seed-job` and `castrol register-plate` — create one job by hand; register and activate a plate and its uniform reference |
| [`src/castrol_pipeline/config.py`](src/castrol_pipeline/config.py) | every key, endpoint, pinned model id and feature flag, from env |
| [`spikes/prototype.py`](spikes/prototype.py) | the standalone one-video script; no DB, no orchestrator |
| [`scripts/apply_migration.py`](scripts/apply_migration.py) | applying a migration (the Supabase MCP server here is read-only) |

### Stages

| File | Handles |
|---|---|
| [`stages/base.py`](src/castrol_pipeline/stages/base.py) | the `Stage` protocol, the DAG, `JobContext`, `StageResult`, `AsyncSubmission` |
| [`stages/real.py`](src/castrol_pipeline/stages/real.py) | all eight real stages, one class each, and the `REAL_STAGES` registry |
| [`stages/vendors.py`](src/castrol_pipeline/stages/vendors.py) | **everything that talks to a provider** — image, video, voice; submit and poll |
| [`stages/media.py`](src/castrol_pipeline/stages/media.py) | **everything local and free** — ffprobe, mp3, the Pillow card, the ffmpeg composite |
| [`stages/stubs.py`](src/castrol_pipeline/stages/stubs.py) | no-spend doubles for the same protocol (`USE_STUB_STAGES=true`) |

### Shared

| File | Handles |
|---|---|
| [`common/budget.py`](src/castrol_pipeline/common/budget.py) | **the only path to a paid vendor** — `reserve()`, `vendor_call()`, and the rate constants |
| [`common/s3.py`](src/castrol_pipeline/common/s3.py) | key builders, both backends, presigning, server-side copy, `cdn_url` |
| [`common/db.py`](src/castrol_pipeline/common/db.py) | the pool, transaction scope, the `SKIP LOCKED` claim, `mark_*`, the reaper |
| [`common/hashing.py`](src/castrol_pipeline/common/hashing.py) | canonical JSON and every `input_hash` builder — the only module that serialises for hashing |
| [`common/events.py`](src/castrol_pipeline/common/events.py) | durable `job_events`, and the scrubber that keeps credentials out of them |
| [`common/logging.py`](src/castrol_pipeline/common/logging.py) | structlog to stdout, `job_id` bound inside a job context |
| [`common/errors.py`](src/castrol_pipeline/common/errors.py) | reject codes and the stage error taxonomy |

### Content and data shaping

| File | Handles |
|---|---|
| [`prep/script.py`](src/castrol_pipeline/prep/script.py) | **the script itself**, the three placeholders, and the spoken-pronunciation overrides |
| [`prep/normalise.py`](src/castrol_pipeline/prep/normalise.py) | phone → E.164, address splitting, numerals and abbreviations for speech |
| [`prep/plates.py`](src/castrol_pipeline/prep/plates.py) | export `outfit` + `background` → plate ids |
| [`intake/`](src/castrol_pipeline/intake/) | the export pull (**CSV, BOM**), validation, dedupe, photo landing |

### Schema and infrastructure

| File | Handles |
|---|---|
| [`0001_init.sql`](supabase/migrations/0001_init.sql) | every table, the enums, the idempotency indexes, RLS deny-all |
| [`0002_budget_and_seed.sql`](supabase/migrations/0002_budget_and_seed.sql) | USD budget caps, `reserve_vendor_call()`, the six plate rows |
| [`0003_cartesia_tts_no_repair.sql`](supabase/migrations/0003_cartesia_tts_no_repair.sql) | voice lane enabled; the repair pass deleted |
| [`0004_runtime_observability.sql`](supabase/migrations/0004_runtime_observability.sql) | per-attempt cost, `assets.cdn_url`, `job_events`, the `job_costs` view |
| [`0005_admin_review_and_export_copy.sql`](supabase/migrations/0005_admin_review_and_export_copy.sql) | `job_reports` (the panel's only write) and the raw export copy |
| [`0006_real_export_schema.sql`](supabase/migrations/0006_real_export_schema.sql) | the real CSV columns — `card_phone_e164`, `mechanic_id_verified`, the client's own row `id` |
| [`0007_usage_views_for_the_panel.sql`](supabase/migrations/0007_usage_views_for_the_panel.sql) | `job_usage` / `daily_usage` — duration only, no cost or vendor column to leak |
| [`0008_job_usage_video_url.sql`](supabase/migrations/0008_job_usage_video_url.sql) | the delivered URL on `job_usage` |
| [`0009_plate_uniform_reference.sql`](supabase/migrations/0009_plate_uniform_reference.sql) | `plates.uniform_ref_key` — the image edit's third input |
| [`0010_bill_on_the_render_not_the_trim.sql`](supabase/migrations/0010_bill_on_the_render_not_the_trim.sql) | `job_usage.video_seconds` reads `video_raw` — the render, not the trimmed file |
| [`0011_raise_the_daily_cost_caps.sql`](supabase/migrations/0011_raise_the_daily_cost_caps.sql) | `daily_cost_cap_usd` → $5000 / $500 / $500 |
| [`0012_raise_the_call_caps_to_match.sql`](supabase/migrations/0012_raise_the_call_caps_to_match.sql) | `daily_call_cap` → 5000 / 40000 / 25000, so the cost cap is what binds |
| [`0013_refunded_runs.sql`](supabase/migrations/0013_refunded_runs.sql) | `stage_runs.refunded`; `job_costs` counts what was billed, not what was reserved |
| [`0014_ceil_the_billed_second.sql`](supabase/migrations/0014_ceil_the_billed_second.sql) | `video_seconds` **ceiled per render**, so `daily_usage` sums already-billed integers |
| [`infra/s3-lifecycle.json`](infra/s3-lifecycle.json) | what expires and when — the 180-day delivery rule |
| [`infra/README.md`](infra/README.md) | the AWS commands you run by hand, and why the IAM user cannot |
| [`.env.example`](.env.example) | every variable, documented |

### Tests

| File | Pins |
|---|---|
| [`tests/test_storage_keys.py`](tests/test_storage_keys.py) | the prefix and CDN-URL rules — the 403 that reads like a permissions failure |
| [`tests/test_events.py`](tests/test_events.py) | credentials never reach the audit trail; logging never fails a stage |
| [`tests/test_budget_costs.py`](tests/test_budget_costs.py) | cost rounding, on the step that bills per second |
| [`tests/test_hashing.py`](tests/test_hashing.py) | what does and does not force a regeneration |
| [`tests/test_orchestrator_policy.py`](tests/test_orchestrator_policy.py) | retry and terminality decisions |
| [`tests/test_prep.py`](tests/test_prep.py), [`tests/test_intake.py`](tests/test_intake.py) | normalisation and validation rules |
| [`tests/test_export_csv.py`](tests/test_export_csv.py) | the BOM — as plain utf-8 the `id` column silently reads as missing |
| [`tests/test_avatar_prompt.py`](tests/test_avatar_prompt.py) | the motion prompt is hashed as TEXT, so editing it regenerates |
| [`tests/test_image_prompt.py`](tests/test_image_prompt.py) | geometry survives both prompts; the two-image prompt never names a third image |
| [`tests/test_card_fields.py`](tests/test_card_fields.py) | the card prints the CONTACT number, never the WhatsApp one |
| [`tests/test_composite_trim.py`](tests/test_composite_trim.py) | the silent tail is cut, and the billed length is not |
| [`tests/test_deliver_webhook.py`](tests/test_deliver_webhook.py) | delivery is read from the BODY and fails closed |
| [`tests/test_cycle.py`](tests/test_cycle.py) | the pull window's timezone, and the wait loop's sleep |

---

## Cost

Full tables, in dollars and rupees, are in
[`docs/COST_PER_VIDEO.md`](docs/COST_PER_VIDEO.md).

```
standard  cost = $0.014  +  seconds x $0.0360        (25s ≈ $0.94)
pro       cost = $0.014  +  seconds x $0.0720        (25s ≈ $1.82)
                 ↑ one image        ↑ video + audio $0.00087
```

| Runtime | Standard | Pro |
|---:|---:|---:|
| 20s | $0.751 | $1.471 |
| 25s | $0.936 | $1.836 |
| 30s | $1.120 | $2.200 |

The video step is **~96%** of it and bills per output second, so **runtime is
the only lever that matters**. Audio and image together are ~4%. The provider
ceils to
whole seconds: 24.8s bills as 25s.

`kling/ai-avatar-pro` doubles the total and is the **only way to get 1080p** —
standard returns 720x1280 whatever it is fed, pro returns 1072x1920, and there
is no resolution parameter on either. `VIDEO_MODEL_ID` is the whole switch, and
`Settings.video_is_pro` derives the billing rate from it.

Rates: `gpt-image-2` $0.014/image @2K; video $0.036/output second standard
and $0.072 pro; voice 1 credit per **character** at 100K credits per $5
($0.00005/char, no block rounding).

`common/budget.py` pins exactly these — corrected in `cf00e4a`, so a
reservation and an invoice agree and `job_costs` can be quoted. Measured over
11 real renders: 27–31s billed, avg 29.4s, **$1.0567 per render and $1.093
all-in per video**. Re-measure after any batch.

**Caps live in `vendor_limits`; `daily_cost_cap_usd` is the binding one.**
Migrations `0011` and `0012` set them to $5000 on `kie_video` and $500 on the
other two, with `daily_call_cap` lifted to 5000 / 40000 / 25000 so the cost cap
trips first for every vendor. Worst case $6000/day against an observed ~$44 —
a runaway guard, not a budget. The cap day is IST and both timer cycles share
one bucket.

Spend is recorded per **attempt** on `stage_runs`, not per job, because a
per-job total is what lets a retry bug run for a week unseen. But a retried
video step does **not** cost twice: every failed vendor job refunds its
credits, so migration `0013` marks a terminal failure `refunded` and
`job_costs.cost_usd` counts only what was actually billed, carrying what came
back as `refunded_usd` so the difference stays auditable. Before that a job
which failed twice before succeeding read roughly three times its real bill —
in the expensive-looking direction, out of the view a client quote is built
from.

`castrol costs` reads the `job_costs` view. `vendor_usage` is the independent
count the budget guard keeps, and it is deliberately **not** refund-adjusted:
a cap that forgave failures is one a retry loop can walk straight through. The
two disagreeing on *reservations* means a paid call happened outside the guard;
the two disagreeing on *cost* is just refunds, and expected.

---

## Docs

| | |
|---|---|
| [`docs/TECH_DESIGN.md`](docs/TECH_DESIGN.md) | how it is built — modules, state machine, invariants |
| [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) | scope, client decisions, risks |
| [`docs/TALKING_HEAD_PIPELINE_REFERENCE.md`](docs/TALKING_HEAD_PIPELINE_REFERENCE.md) | prod-measured evidence from the existing BeHooked backend — the source of most invariants |
| [`docs/CARTESIA_API_DOCS.md`](docs/CARTESIA_API_DOCS.md) | Voice provider API reference — implemented in [`stages/vendors.py`](src/castrol_pipeline/stages/vendors.py) |
| [`docs/COST_PER_VIDEO.md`](docs/COST_PER_VIDEO.md) | the per-video arithmetic, in dollars and rupees |
| [`deploy/README.md`](deploy/README.md) | **the EC2 runbook** — provision, install, operate, read the logs |
| [`docs/EC2_DEPLOYMENT.md`](docs/EC2_DEPLOYMENT.md) | **the deployment as built** — resource ids, decisions taken, what was verified |
| [`infra/README.md`](infra/README.md) | AWS setup commands you run by hand |
| [`panel/DEPLOY.md`](panel/DEPLOY.md) | how the admin panel reached Vercel, and why |
| [`spikes/README.md`](spikes/README.md) | the Phase 0 spike list and what each one killed |
| [`CLAUDE.md`](CLAUDE.md) | working rules and the 33 invariants, each naming the file that enforces it |

---

## Status

The pipeline runs end to end under the orchestrator against real Supabase, real
S3 and the real CDN. Every stage is implemented; `USE_STUB_STAGES=true` still
swaps in deterministic fakes to exercise the DAG without spending. Migrations
0001–0014 are applied.

Verified: seed → prep → composite → checks → publish → deliver on a real job,
with the delivered CDN URL returning 200. The three paid stages are the same
calls the prototype proved, now under budget reservation and cost recording.
Nine jobs have completed for real, at an average 26.9s render.

**Deployed to EC2** (2026-09-15). `i-0d7560cd333c94cde`, `t3.medium` in
`ap-south-1`, running the CI-built container behind a systemd timer. The image,
the `.env` mount and the database connection are all verified on the box —
see [`docs/EC2_DEPLOYMENT.md`](docs/EC2_DEPLOYMENT.md).

Open:

- **The timer is not armed.** Everything is installed and `castrol doctor`
  passes on the instance, but nothing runs on a schedule yet, so nothing has
  spent. The first cycle will render every new submission in the pull window.
- **`DELIVERY_ENABLED` is false.** The deliver stage logs what it would POST and
  sends nothing. Turning it on is deliberate (invariant 28); the first cycle
  afterwards reopens the whole suppressed backlog at once (invariant 32).
- **The client asked for 1080p on 2026-09-12** and production still runs
  `kling/ai-avatar-standard`, which returns 720x1280. `kling/ai-avatar-pro`
  returns 1072x1920 and the model id is the only switch — it doubles the
  per-second rate, so this is a cost decision, not an oversight.
- **Geometry drift** — spike 0.3 — remains the open unknown. Nothing in the
  existing backend holds a subject at a fixed pixel scale across an image edit,
  so there is no prior art to lean on. What is no longer missing is the
  measurement: the plates are 1152x2048, the card rect is fixed at
  `y 72.27%..87.00%`, and across nine renders the hands sat at 60–70% of frame
  height. The drift itself has still not been measured per job, and no check
  looks for it.

Closed since the last revision:

- **The video rates agree.** `common/budget.py` pins $0.036/$0.072 (`cf00e4a`)
  and every doc here now matches. `docs/TALKING_HEAD_PIPELINE_REFERENCE.md`
  still carries $0.04/s and is correct to: it records what the OTHER BeHooked
  stack measured and is not this pipeline's config. Reconcile against the
  provider dashboard after any batch regardless.
