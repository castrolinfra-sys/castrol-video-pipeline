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
| 3 | **Name** | export `user_name` | **spoken** and on the card |
| 4 | **Workshop name** | export `workshop_name` | **spoken** and on the card |
| 5 | **Location** | export `address` (`Locality, City`) | **spoken** and on the card |
| 6 | **Phone** | export `whatsapp_number` | **card only** — never spoken |

Only 3–5 vary inside the script; the rest of the script is identical for every
mechanic. The phone number appears on the card but is never read aloud.

---

## The pipeline

```
      plate + mechanic photo
              │
      prep    fill the script, pick the spoken locality        free
        ├───────────────┐
  [A] audio        [B] image                                   $0.02 / $0.014
  Cartesia         apimart gpt-image-2 @2K
  sonic-3.6        person replacement on the plate
        └───────┬───────┘
  [C] video    kie kling-avatar-standard    (8-20 MINUTES)     $0.04 / second
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
| `audio` **A** | `AudioStage` | [`vendors.py`](src/castrol_pipeline/stages/vendors.py) `cartesia_tts` | [`media.py`](src/castrol_pipeline/stages/media.py) `to_mp3`, `probe_duration_seconds` | $0.00005/char |
| `image` **B** | `ImageStage` | [`vendors.py`](src/castrol_pipeline/stages/vendors.py) `apimart_submit` / `apimart_poll` | — | $0.014 |
| `video` **C** | `VideoStage` | [`vendors.py`](src/castrol_pipeline/stages/vendors.py) `kie_submit` / `kie_poll` | — | $0.04/s |
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
[`stages/vendors.py`](src/castrol_pipeline/stages/vendors.py) asks for natural
open-palm gestures **at chest height** — the card is an opaque overlay over
70–87% of frame height, so a waist-level gesture happens behind it and the
viewer sees a hand enter frame and vanish.

The prompt text is part of the video `input_hash`, not a version string you can
forget to bump. Editing it therefore regenerates — at $0.04 per output second
for every job that has not completed.

### Storage and URLs

Two kinds of read, and the difference is not cosmetic:

| | For | Lifetime |
|---|---|---|
| **Presigned GET** | handing a working artefact to a vendor — apimart and kie fetch inputs by URL | 6h, method-bound, private |
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

> This project uses a **dedicated set of accounts** — GitHub, AWS, Supabase,
> Vercel, apimart, kie and Cartesia are all separate from other BeHooked
> projects. See [`CLAUDE.md`](CLAUDE.md).

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

**One video by hand.** The manual-entry path — a single mechanic, a plate test,
a client sample — without the export API in the way. Creates the rows and lands
the photo in S3; runs nothing. Idempotent on (photo, phone).

```bash
uv run castrol seed-job --photo spikes/in/mechanic2.jpg --plate spikes/in/plate_bg2.png --name "Amit Kumar" --workshop "Ganesh Car Service" --address "Beturkar Pada, Opposite New National Hospital, Andheri" --phone 9773128990 --uniform polo --background bg2_dark_sedan
```

`--address` is what the **card** prints. What the voice **says** defaults to the
last segment of it (`Andheri`), so landmarks are not read aloud; override with
`--spoken-place`.

**Run it.** `drain` sweeps every stage and the poller until nothing moves —
convenient locally; production uses N workers and a separate poller.

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

**Bulk intake** from the client export (still written against the pre-CSV
schema — see Status):

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
| [`src/castrol_pipeline/cli.py`](src/castrol_pipeline/cli.py) | every `castrol <cmd>` — `doctor`, `seed-job`, `drain`, `work`, `poll`, `schedule`, `intake`, `show`, `events`, `costs`, `report` |
| [`src/castrol_pipeline/orchestrator.py`](src/castrol_pipeline/orchestrator.py) | readiness, claiming, retries, the poller, and **every write to `jobs`** |
| [`src/castrol_pipeline/seed.py`](src/castrol_pipeline/seed.py) | `castrol seed-job` — create one job by hand, register and activate a plate |
| [`src/castrol_pipeline/config.py`](src/castrol_pipeline/config.py) | every key, endpoint, pinned model id and feature flag, from env |
| [`spikes/prototype.py`](spikes/prototype.py) | the standalone one-video script; no DB, no orchestrator |
| [`scripts/apply_migration.py`](scripts/apply_migration.py) | applying a migration (the Supabase MCP server here is read-only) |

### Stages

| File | Handles |
|---|---|
| [`stages/base.py`](src/castrol_pipeline/stages/base.py) | the `Stage` protocol, the DAG, `JobContext`, `StageResult`, `AsyncSubmission` |
| [`stages/real.py`](src/castrol_pipeline/stages/real.py) | all eight real stages, one class each, and the `REAL_STAGES` registry |
| [`stages/vendors.py`](src/castrol_pipeline/stages/vendors.py) | **everything that talks to a provider** — apimart, kie, Cartesia; submit and poll |
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
| [`intake/`](src/castrol_pipeline/intake/) | the export pull, validation, dedupe, photo landing — **still on the pre-CSV schema** |

### Schema and infrastructure

| File | Handles |
|---|---|
| [`0001_init.sql`](supabase/migrations/0001_init.sql) | every table, the enums, the idempotency indexes, RLS deny-all |
| [`0002_budget_and_seed.sql`](supabase/migrations/0002_budget_and_seed.sql) | USD budget caps, `reserve_vendor_call()`, the six plate rows |
| [`0003_cartesia_tts_no_repair.sql`](supabase/migrations/0003_cartesia_tts_no_repair.sql) | Cartesia enabled; the repair pass deleted |
| [`0004_runtime_observability.sql`](supabase/migrations/0004_runtime_observability.sql) | per-attempt cost, `assets.cdn_url`, `job_events`, the `job_costs` view |
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

---

## Cost

Measured, at 25s of runtime:

```
cost = $0.014  +  seconds x $0.040886
       ↑ one image          ↑ video $0.0400 + audio $0.000886
```

| Runtime | Total | All-in $/sec |
|---:|---:|---:|
| 20s | $0.832 | $0.0416 |
| 25s | $1.036 | $0.0414 |
| 30s | $1.241 | $0.0414 |

The video step is **96.5%** of it and bills per output second, so **runtime is
the only lever that matters**. Audio and image together are 3.5%. Using
`kling/ai-avatar-pro` instead of standard doubles the total.

Rates: apimart `gpt-image-2` $0.014/image @2K; kie `kling/ai-avatar-standard`
$0.04/output second; Cartesia 1 credit per **character** at 100K credits per $5
($0.00005/char, no block rounding).

Spend is recorded per **attempt** on `stage_runs`, not per job — a job that
retried the video step really did pay twice, and a per-job total that hides
that is what lets a retry bug run for a week. `castrol costs` reads the
`job_costs` view; `vendor_usage` is the independent count the budget guard
keeps, and the two disagreeing means a paid call happened outside the guard.

---

## Docs

| | |
|---|---|
| [`docs/TECH_DESIGN.md`](docs/TECH_DESIGN.md) | how it is built — modules, state machine, invariants |
| [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) | scope, client decisions, risks |
| [`docs/TALKING_HEAD_PIPELINE_REFERENCE.md`](docs/TALKING_HEAD_PIPELINE_REFERENCE.md) | prod-measured evidence from the existing BeHooked backend — the source of most invariants |
| [`docs/CARTESIA_API_DOCS.md`](docs/CARTESIA_API_DOCS.md) | Cartesia reference — implemented in [`stages/vendors.py`](src/castrol_pipeline/stages/vendors.py) |
| [`infra/README.md`](infra/README.md) | AWS setup commands you run by hand |
| [`spikes/README.md`](spikes/README.md) | the Phase 0 spike list and what each one killed |
| [`CLAUDE.md`](CLAUDE.md) | working rules and the 28 invariants, each naming the file that enforces it |

---

## Status

The pipeline runs end to end under the orchestrator against real Supabase, real
S3 and the real CDN. Every stage is implemented; `USE_STUB_STAGES=true` still
swaps in deterministic fakes to exercise the DAG without spending.

Verified: seed → prep → composite → checks → publish → deliver on a real job,
with the delivered CDN URL returning 200. The three paid stages are the same
calls the prototype proved, now under budget reservation and cost recording.

Open:

- **Plates.** Being authored. The card position is calibrated for 1080×1920;
  `seed-job` warns on anything else, because apimart reframes a non-9:16 plate
  by inventing new ceiling and floor.
- **Intake** is still written against the pre-CSV export schema (the real export
  returns CSV, has no `image_face_count`, and carries two phone fields). Use
  `seed-job` until it is reworked.
- **Standard vs pro** avatar — pro doubles the total.
- **`DELIVERY_ENABLED` is false.** The deliver stage logs what it would POST and
  sends nothing until the client confirms the webhook contract.
