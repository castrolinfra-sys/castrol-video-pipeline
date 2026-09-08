# Castrol MAGNATEC video pipeline — technical design

**Status:** Phase 0 — foundation scaffolded, spikes not yet run
**Companion doc:** [PROJECT_PLAN.md](PROJECT_PLAN.md) — scope, client decisions, risks
**This doc:** how it is built. Module layout, interfaces, state machine, invariants.

Where the two disagree, the project plan wins on *what* and this doc wins on *how*.

---

## 1. Scope

A batch video factory. Pull mechanic submissions from the client's export API,
generate a personalised vertical promo video per mechanic, host it, POST the URL
back to the client's delivery webhook.

**Not ours:** WhatsApp, Meta Cloud API, consent capture, conversation state,
message delivery. The client runs that on Interakt. We see two HTTP contracts and
nothing else.

**Non-goals for this release:** female voice/plates, aspect-ratio variants,
duplicate-phone handling, any notification or alerting channel, real-time
generation. Everything is nightly batch.

---

## 2. System shape

Four processes, one codebase, one database. Nothing talks to anything else
except through Postgres.

| Process | Trigger | Job |
|---|---|---|
| `intake` | nightly cron | pull export window → validate → land photos in S3 → insert submissions + jobs |
| `worker` | long-running, N instances | claim `stage_runs`, execute one stage, release |
| `poller` | long-running, 1 instance | reconcile in-flight async vendor tasks (stage C) |
| `reporter` | end of batch | roll up counts into `batches`, close the run |

The admin panel (Vercel) is read-only over the same Postgres. It is not in the
critical path and cannot be.

**Why Postgres-as-queue and not SQS/Redis:** the state has to be inspectable and
resumable anyway, the volume is a few hundred rows a night, and `FOR UPDATE SKIP
LOCKED` is sufficient. A second piece of infrastructure would buy nothing and
add a way for queue state and DB state to disagree.

```
  client export API
        │  (daily pull, from/to)
        ▼
  ┌──────────┐   photos ──▶ S3 (system of record)
  │  intake  │
  └────┬─────┘
       │ submissions + jobs + stage_runs(prep)
       ▼
  ┌──────────────── Postgres ────────────────┐
  │  jobs · stage_runs · assets · checks     │◀──┐
  └────┬─────────────────────────────────────┘   │
       │ claim                                    │ reconcile
       ▼                                          │
  ┌──────────┐        submit + release      ┌──────────┐
  │  worker  │ ───────────────────────────▶ │  poller  │
  └────┬─────┘                              └──────────┘
       │ final mp4
       ▼
  S3 (private) ──▶ CDN ──▶ POST client webhook {phone, videoLink}
```

---

## 3. Repo layout

Every path below is real. If you are looking for where something happens, this
table is the index.

```
src/castrol_pipeline/
  cli.py               typer entrypoints — every `castrol <cmd>` lands here
  config.py            pydantic-settings; every key, endpoint and pinned id from env
  orchestrator.py      readiness, claiming, retries, job state, the poller
  seed.py              create one job by hand from local files (`castrol seed-job`)
  common/
    db.py              psycopg pool, claim/release helpers, transaction scope
    logging.py         structlog to stdout; job_id bound on every line
    events.py          DURABLE log -> job_events, and the credential scrubber
    hashing.py         canonical json + sha256; the only place that serialises for hashing
    s3.py              key builders, put/get/copy, presign, cdn_url
    budget.py          reserve_vendor_call wrapper — the only path to a paid vendor
    errors.py          reject codes + stage error taxonomy
  intake/
    export_client.py   date-range pull, pinned timestamp format
    validate.py        validation rules, reject codes, no repair
    media.py           verbatim-URL fetch, magic-byte check, S3 landing
    dedupe.py          media_key / submission_hash
  prep/
    script.py          the script template, fill, spoken overrides
    normalise.py       phone, address, numerals, speech expansion
    plates.py          export background+outfit -> plate ids
  stages/
    base.py            Stage protocol, the DAG, JobContext, StageResult
    real.py            ALL EIGHT real stages + REAL_STAGES registry
    stubs.py           deterministic fakes (USE_STUB_STAGES=true), no spend
    vendors.py         apimart / kie / Cartesia HTTP clients, submit + poll
    media.py           ffprobe, mp3, the Pillow card, the ffmpeg composite
scripts/
  apply_migration.py   the write path for migrations (the MCP server is read-only)
supabase/migrations/   numbered SQL, forward-only, no down migrations
infra/                 AWS setup you run by hand (lifecycle rules, bucket posture)
spikes/                throwaway; imports src, is never imported BY src
tests/                 pytest; no network, no database
```

There is no `stages/audio.py`, `image.py`, `video.py` or `card.py`, and no
`publish/` package. An earlier draft of this document specified one file per
stage. In practice the eight stages are ~100 lines each and share the same
helpers, so they live together in [`stages/real.py`](../src/castrol_pipeline/stages/real.py)
and the shared work is split by KIND rather than by stage:
[`vendors.py`](../src/castrol_pipeline/stages/vendors.py) is everything that
talks to a provider, [`media.py`](../src/castrol_pipeline/stages/media.py) is
everything local and free.

### Which file handles which stage

| Stage | Implementation | Talks to | Costs |
|---|---|---|---|
| `prep` | `stages/real.py` → `PrepStage`, using `prep/script.py` | — | free |
| `audio` (A) | `stages/real.py` → `AudioStage`, via `stages/vendors.py:cartesia_tts` | Cartesia | $0.00005/char |
| `image` (B) | `stages/real.py` → `ImageStage`, via `vendors.py:apimart_submit/_poll` | apimart | $0.014 |
| `video` (C) | `stages/real.py` → `VideoStage`, via `vendors.py:kie_submit/_poll` | kie | $0.04/s |
| `composite` (D) | `stages/real.py` → `CompositeStage`, using `stages/media.py` | — | free |
| `checks` | `stages/real.py` → `ChecksStage` | — | free |
| `publish` | `stages/real.py` → `PublishStage`, using `common/s3.py` | S3 + CDN | free |
| `deliver` | `stages/real.py` → `DeliverStage` | client webhook | free |

Rule: **`src/` never imports `spikes/`.** The reverse is allowed and is now
used deliberately — [`spikes/prototype.py`](../spikes/prototype.py) imports
[`stages/media.py`](../src/castrol_pipeline/stages/media.py) so the card and the
ffmpeg settings have ONE implementation. The card geometry was tuned against
real client review; a second copy in the spike would have drifted on the first
revision, and the spike is what the client sees.

Spikes are otherwise allowed to be ugly.

---

## 4. Data model

Defined in [`0001_init.sql`](../supabase/migrations/0001_init.sql), extended by
[`0002`](../supabase/migrations/0002_budget_and_seed.sql) (USD budget caps),
[`0003`](../supabase/migrations/0003_cartesia_tts_no_repair.sql) (Cartesia, no repair
pass) and [`0004`](../supabase/migrations/0004_runtime_observability.sql) (per-attempt
cost, `assets.cdn_url`, `job_events`, the `job_costs` view).
Notes on the decisions that are not obvious from the DDL:

**`job_id` (uuid) is the primary key of the system.** `mechanic_id` from the
client is stored as an opaque string, carried through, and never joined on —
it is inconsistently formatted and `has_mechanic_id` disagrees with it.
`phone_e164` is the *client's* join key because the delivery webhook accepts
nothing else, but it is not unique on our side (duplicate phones are out of
scope this release, and making it unique would reject rows we want to keep).

**Timestamps are stored twice.** `created_at_ist_raw` keeps the exact string;
`created_at_ist` holds the value parsed with the format pinned in
`EXPORT_TIMESTAMP_FORMAT`. If the format turns out to be wrong we can reparse
from the raw column instead of re-pulling.

**`image_url_raw` is stored byte-exact.** Azure signs over exact bytes. The
column exists so nothing in the stack ever needs to rebuild the URL from parts.

**`reserve_vendor_call()` is a database function, not application code.** It is
the only thing standing between a retry bug and a real bill, so it has to be
atomic and it has to fail closed. A vendor with no `vendor_limits` row cannot
be called at all.

---

## 5. Job lifecycle

**Implemented in** [`orchestrator.py`](../src/castrol_pipeline/orchestrator.py) — `ready_stages`, `schedule_ready`, `execute_one`, `advance_job`. State lives in `stage_runs`; `jobs.current_stage` is display only and is never used for routing.

```
prep → audio ─┐
              ├→ video → composite → checks → publish → deliver
      image ──┘
```

`audio` and `image` are independent and run ahead so video workers never wait
upstream. `video` is the bottleneck: async submit, poller reconciles.

A job advances when every stage it depends on has a `succeeded` row. Stage
readiness is computed, not stored — `jobs.current_stage` is a display
convenience for the panel and is never used for routing.

**Terminal states.** A job is `failed` when any stage exhausts
`STAGE_MAX_ATTEMPTS`. There is no partial delivery: we never POST a video that
did not complete every stage. A failed job sits in Postgres and appears in the
panel. Nothing is sent to the client — the webhook has no failure channel.

**There is no repair pass.** A second lipsync pass was considered and dropped:
quality is solved in the main flow, not by re-running it. If stage C output is
unacceptable, the fix is the stage C inputs — audio, plate, prompt — not a
patch stage.

---

## 6. Idempotency

**Implemented in** [`common/hashing.py`](../src/castrol_pipeline/common/hashing.py) (the hash builders — the only module allowed to serialise for hashing) and [`common/db.py`](../src/castrol_pipeline/common/db.py) (`find_succeeded_run`, `enqueue_stage_run`). The partial unique indexes that enforce it are in [`0001_init.sql`](../supabase/migrations/0001_init.sql).

Every stage declares an `input_hash` over exactly the things that would change
its output. A stage with a `succeeded` row at the same hash is skipped. A
failure at C therefore never re-runs A and B, and a re-run after a code change
only redoes the stages whose inputs actually moved.

| Stage | `input_hash` = sha256 of |
|---|---|
| prep | canonical(`submissions.raw`) + `SCRIPT_VERSION` + normalise rules version |
| audio | script text + `TTS_VOICE_ID` + `TTS_MODEL_ID` |
| image | plate sha256 + source photo sha256 + prompt version + `IMAGE_EDIT_MODEL_ID` |
| video | image_edit sha256 + audio sha256 + `VIDEO_MODEL_ID` + params |
| composite | video-in sha256 + canonical(card payload) + card template version |
| publish | video_final sha256 |
| deliver | cdn_url + `phone_e164` |

Model ids and prompt/template versions are **in the hash on purpose**: changing
a model id is what makes a re-run regenerate rather than skip.

Canonical JSON means sorted keys, no whitespace, UTF-8. Implemented once in
`common/hashing.py`; nothing else may serialise for hashing.

---

## 7. Concurrency

**Implemented in** [`common/db.py`](../src/castrol_pipeline/common/db.py) — `_CLAIM_SQL` (`FOR UPDATE SKIP LOCKED`), `reap_stuck_claims` — and [`orchestrator.py`](../src/castrol_pipeline/orchestrator.py) (`drain_stage`, `poll_once`, `_fail_if_overdue`).

Per-stage queues with independent semaphores, so a stall in `video` does not
starve `audio`.

Claiming, in one statement:

```sql
UPDATE stage_runs
   SET status = 'claimed',
       claimed_by = %(worker)s,
       claimed_at = now(),
       attempts = attempts + 1
 WHERE id = (
       SELECT id
         FROM stage_runs
        WHERE stage = %(stage)s
          AND status = 'pending'
          AND next_attempt_at <= now()
        ORDER BY next_attempt_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
 )
RETURNING *;
```

`attempts` increments at claim time, not at failure time. A worker that dies
mid-stage still burns an attempt, which is what we want — otherwise a crash
loop retries forever.

**Connection note.** Use the Supabase *session* pooler (5432). Transaction
pooling (6543) cannot hold `FOR UPDATE ... SKIP LOCKED` semantics across
statements the way the reaper expects, and prepared-statement caching breaks
against it — if 6543 is unavoidable, set `statement_cache_size=0`.

**Stuck claims.** A reaper resets rows `claimed` for longer than the stage
timeout back to `pending` with backoff. Without it a worker OOM silently parks
a job forever.

**Async stages never block a worker.** Stage C submits, writes `vendor_task_id`,
sets status `running`, and releases. The poller reconciles. A worker blocked on
a 4-minute poll is a worker not doing the other 200 jobs.

---

## 8. Vendor budget

**Implemented in** [`common/budget.py`](../src/castrol_pipeline/common/budget.py) (the wrapper and the rate constants) over `reserve_vendor_call()`, defined in [`0002_budget_and_seed.sql`](../supabase/migrations/0002_budget_and_seed.sql) and amended by [`0003`](../supabase/migrations/0003_cartesia_tts_no_repair.sql) and [`0004`](../supabase/migrations/0004_runtime_observability.sql). Per-attempt spend is recorded on `stage_runs` and summed by the `job_costs` view.

Every outbound paid call goes through `common/budget.py`, which calls
`reserve_vendor_call(vendor, cost_usd, seconds)` and refuses the call on
`false`.

**Cap dollars, not calls.** One 35s video costs ~$1.51, and the avatar step is
**93–96% of it** — priced per output second, not per call:

| Step | Model / lane | Cost | Share |
|---|---|---:|---:|
| Image edit | `gpt-image-2-max`, apimart, 2K | $0.012 | 0.8% |
| TTS | ~550 chars, billed as 1k | $0.100 | 6.6% |
| Avatar | `kling-avatar-v2` standard, kie, 35s | **$1.400** | **92.6%** |
| | `pro` variant instead | $2.912 total | avatar = 96% |

A call-count cap bounds volume but bounds *spend* only within ~3× (script
length) × ~2× (standard vs pro). So `vendor_limits` carries both, and
`daily_cost_cap_usd` is the one that matters. Capping the image and TTS steps
is rounding error — those caps exist purely as runaway guards.

`require_cost_estimate` is set on every per-second vendor: a reservation of
zero is **refused**. This closes a specific trap — `kling-avatar-v2`'s
`fallback_duration` is 5 seconds, so a failed duration probe would bill 5s for
a 35s video and no cap would ever notice.

Budgets are keyed per logical stage (`apimart_image`, `kie_video`, `tts`,
`tts`) rather than per provider account, so one looping stage cannot consume
the whole day's spend.

Denied calls fail the stage with `BUDGET_EXHAUSTED` and are **not** retried
within the same day. This is a hard stop, not a throttle.

### Reserving is not the same as not double-charging

**Neither gateway supports an idempotency key on submit.** No such field exists
on kie or apimart. A network-level retry of a submit creates a second provider
job and a second charge, and the budget function cannot see it — it reserved
once. Dedupe before the HTTP call, and on an ambiguous submit *reconcile*
rather than resubmit.

The same reasoning bounds `STAGE_CLAIM_TIMEOUT_S`: if the reaper requeues a row
whose provider call is still running, the retry pays twice. It must exceed the
longest a worker can legitimately hold a claim. Only `is_async = False` stages
hold one — async stages submit, store `vendor_task_id`, and release to
`running`, which the reaper does not touch. **The image stage is currently
`is_async = False` and should become async when the real stage lands**, since
apimart is poll-only with a 2700s ceiling and an observed 644s worst case.

---

## 9. S3 layout

**Implemented in** [`common/s3.py`](../src/castrol_pipeline/common/s3.py) — key builders, both backends, presigning, `cdn_url`. Lifecycle rules are [`infra/s3-lifecycle.json`](../infra/s3-lifecycle.json), applied by hand per [`infra/README.md`](../infra/README.md). Key rules are pinned by [`tests/test_storage_keys.py`](../tests/test_storage_keys.py).

```
s3://<bucket>/castrol/
  plates/<uniform>_<bg>/<sha256>.png       frozen, approved
  jobs/<job_id>/source.<ext>               mechanic photo, our copy
  jobs/<job_id>/audio.wav
  jobs/<job_id>/image_edit.png
  jobs/<job_id>/video_raw.mp4
  jobs/<job_id>/card.png
  jobs/<job_id>/video_final.mp4
  deliver/<uuid4>/video.mp4                the only key the client ever sees
```

`deliver/` is a separate prefix with a fresh UUID, deliberately not `job_id`:

- the public URL leaks no internal identifier and is not enumerable
- the 180-day lifecycle rule targets `deliver/` alone, so expiring a delivered
  link never destroys the working artefacts we would need to diagnose it

**The bucket is private.** Nothing is public-read. Two different URL kinds do
two different jobs, and they are not interchangeable:

| Use | URL kind | Lifetime |
|---|---|---|
| provider inputs (plate, photo, MP3) | **presigned** | 6 h |
| delivered video | **CDN** | until the object is deleted |

*Provider inputs stay presigned.* Those objects are mechanic face photos
joinable to phone numbers, and the short lifetime is the point — a CDN URL for
a source photo is a permanent public link to someone's face, created as a side
effect of making their video. Providers fetch each object exactly once, so edge
caching buys nothing to trade against that. The only real risk is signing too
*short*: a job can sit queued ~45 min, hence 6 h rather than the 1 h that has
bitten this stack before.

*Delivered videos use the CDN,* because presigning cannot express six months —
SigV4 caps at 7 days, and a URL signed with EC2 instance-role credentials dies
with the session token, typically within the hour, regardless of the expiry
requested. A delivered link stops working because the **object is deleted on
schedule** (`infra/s3-lifecycle.json`, 180 days on `castrol/deliver/`), not
because a signature lapsed. Security is the unguessable key, so the uuid must
never be derived from a phone number or a `job_id`.

CloudFront is live and verified. The distribution has **no Origin Path**, so
the full S3 key including the `castrol/` prefix must appear in the URL:
`https://<cdn>/castrol/deliver/<uuid4>/video.mp4` returns 200,
`https://<cdn>/deliver/<uuid4>/video.mp4` returns 403.

`castrol/jobs/` is deliberately never expired. Those working artefacts are what
diagnoses a complaint about a video that shipped five months ago; expiring the
delivered copy must not destroy the evidence.

---

## 10. Stage contracts

**Defined in** [`stages/base.py`](../src/castrol_pipeline/stages/base.py) (protocol, DAG, `JobContext`, `StageResult`). **Implemented in** [`stages/real.py`](../src/castrol_pipeline/stages/real.py); the no-spend doubles are [`stages/stubs.py`](../src/castrol_pipeline/stages/stubs.py).

Every stage implements one protocol so a vendor swap is a config change:

```python
class Stage(Protocol):
    name: PipelineStage
    is_async: bool

    def input_hash(self, ctx: JobContext) -> str: ...
    def run(self, ctx: JobContext) -> StageResult: ...
    def poll(self, run: StageRun) -> StageResult | None: ...   # async only
```

`StageResult` carries `output_key`, `sha256`, optional `duration_ms` /
dimensions, and the vendor metadata to record. Stages do not write to `jobs`;
the orchestrator does. Stages do not decide retries; the orchestrator does.

### A — audio
**Cartesia, direct API.** The one deliberate exception to "apimart + kie only",
taken because that intersection has no voice-cloning Hindi lane at all. Hindi
and Gujarati are both prod-verified on Cartesia with a cloned voice.

The voice is **created by hand in the Cartesia dashboard** and referenced by
id. There is no cloning call in the pipeline — no `/voices/clone`, no
per-render clone latency, no voice lifecycle to manage. `TTS_VOICE_ID` is
config, pinned like a model id.

```
POST https://api.cartesia.ai/tts/bytes
Authorization: Bearer $TTS_API_KEY
Cartesia-Version: 2026-05-11
{ "model_id": "sonic-3.5",
  "transcript": "<filled script>",
  "voice": { "mode": "id", "id": "<dashboard voice id>" },
  "output_format": { ... } }
→ raw audio bytes. Synchronous. No task id, no polling, no duration.
```

Two things this stage owns beyond the call, both mandatory:

1. **Emit MP3.** The avatar model's `"Audio size is too large"` is a byte
   limit, not a duration limit, and every observed failure was a WAV.
   Cartesia's default `pcm_f32le` @44.1kHz is ~176 KB/s — 40s is ~7 MB against
   ~640 KB as MP3. Request an MP3 container if Cartesia will emit one;
   otherwise transcode before handing off. Either way stage C never sees a WAV.
2. **Probe the duration with ffmpeg and record it.** Cartesia returns no
   duration, and stage C bills per output second — this probe is a billing
   input, not a convenience. Fail closed to the cap, never to zero.

Billing ceils per 1000 characters with a minimum of 1, so a ~550-character
script bills as a full 1k either way. Roughly $0.10 per video, ~7% of cost.

**Language note:** `language_code` was null on every verified Hindi run, and
the clone call's `language` field does not appear to gate synthesis language.
Do not assume it needs setting; test before adding it.

### B — image
Person replacement on the frozen plate. Prompt is structured
Change / Preserve / Constrain (see PROJECT_PLAN §4.4). Geometry preservation is
not cosmetic: the card sits at a fixed pixel position, so subject scale drift
puts the card over the mechanic's hands.

The plate is a **pose and composition reference, not a frozen asset** — the
chest and sleeve Castrol marks are re-rendered on a torso whose shape varies
per person. They cannot be composited. That is why the logo check is
load-bearing rather than nice-to-have.

### C — video
The `prompt` field is REQUIRED on kie (max 5000 chars) and is not decorative:
it steers expression, head movement and hand gesture. `AVATAR_PROMPT` in
[`stages/vendors.py`](../src/castrol_pipeline/stages/vendors.py) follows the
model's documented shape — subject, expression, motion, style preservation, in
a few sentences — and carries two constraints that come from our own pipeline
rather than from the model: gestures stay at **chest height** so they are not
hidden behind the card overlay (66-82% of frame height), and off the **chest
logo**, which is the point of the video. The text is hashed directly, so
editing it regenerates rather than silently skipping.

`kling-avatar-v2` on kie. Async submit, `vendor_task_id` stored, poller
reconciles. The bottleneck, and 93–96% of the money.

Latency is measured, not guessed: ~256s at 16s of audio, ~607s at 30s,
**~1191s (~20 min) at 39s**. Budget 8–20 minutes for a 30–40s render and size
every timeout above it.

kie's response shape has two traps worth restating: HTTP 200 with `code != 200`
is an error, and `resultJson` is a JSON *string* that must be parsed before
indexing `resultUrls[0]`. Copy the result to our storage inside the handler —
the provider URL's TTL is unmeasured and unrelied-upon.

### D — composite
`ffmpeg` overlay of the rendered card, full duration, fixed geometry. Fully
deterministic — **no generative model ever touches the text.** This is the
single design decision that removes text rendering risk from the pipeline.

---

## 11. Client interface

**Inbound** is [`intake/export_client.py`](../src/castrol_pipeline/intake/export_client.py) and [`intake/runner.py`](../src/castrol_pipeline/intake/runner.py); the photo fetch is [`intake/media.py`](../src/castrol_pipeline/intake/media.py). **Outbound** is `DeliverStage` in [`stages/real.py`](../src/castrol_pipeline/stages/real.py), gated by `DELIVERY_ENABLED`.

> Intake is still written against the pre-CSV export schema. Until it is
> reworked, create jobs with `castrol seed-job` —
> [`seed.py`](../src/castrol_pipeline/seed.py).

### Inbound — export pull

```
GET {CLIENT_EXPORT_URL}?from=YYYY-MM-DD&to=YYYY-MM-DD
```

The pull is **not idempotent**: overlapping windows re-return rows and late
submissions land in later windows. Dedupe is ours, on
`submission_hash = sha256(media_key + phone_e164)` where `media_key` is the
blob path with the query string stripped. Those path segments are unique per
upload, which makes them a stronger key than anything timestamp-derived —
`created_at_ist` has no seconds.

### Photo fetch — the SAS rule

Photos are Azure blob URLs carrying a SAS token. **Treat the URL as opaque
bytes.** Encoding is inconsistent *within a single URL* — colons in `se=` are
percent-encoded while `sig=` carries a raw forward slash. Azure signs over
exact bytes, so any normalisation returns 403, which reads like a permissions
problem rather than a parsing bug.

Concretely:

- pass the stored string to the HTTP client verbatim
- never `urllib.parse.quote` / `unquote` it, never round-trip it through a
  URL-normalising client, never form-decode it (base64 signatures contain `+`,
  which becomes a space)
- never rebuild it from parsed components

`intake/media.py` carries this as a code comment, because it is exactly the
kind of thing a later refactor "fixes".

Validate `Content-Type` **and magic bytes**, never the status code — a
permissions failure can return 200 with an HTML body, which without this check
writes login pages into S3 as `.jpg`.

Download once, at intake, straight to S3. Our copy is the system of record.
All SAS tokens are signed with one storage account key: if the client rotates
it, every URL dies at once regardless of the 2031 expiry. The S3 copy is the
mitigation.

### Outbound — delivery webhook

```
POST {CLIENT_WEBHOOK_URL}
Headers: apikey, Content-Type: application/json
Body:    {"phone": "...", "videoLink": "https://..."}
```

Retry with backoff, response recorded in `deliveries`. There is no failure
channel — a video that never generates is visible in our panel and nowhere
else. That is a decision, not an oversight.

---

## 12. Validation and error taxonomy

**Implemented in** [`intake/validate.py`](../src/castrol_pipeline/intake/validate.py) (reject codes) and [`common/errors.py`](../src/castrol_pipeline/common/errors.py) (stage errors). The retry decision belongs to [`orchestrator.py`](../src/castrol_pipeline/orchestrator.py) `retry_delay_for` — never to a stage.

Rejection happens at intake, with a stable code. Rows are **never repaired
in-pipeline** — a repaired row is a row whose output nobody can explain.

| Code | Rule |
|---|---|
| `NOT_APPROVED` | `image_validation_status != APPROVED` |
| `FACE_COUNT_NOT_1` | `image_face_count != 1` (a group photo passes "face detected" and breaks stage B) |
| `BAD_MIME` | magic bytes not a recognised image type |
| `IMAGE_TOO_SMALL` | short edge < 100px |
| `BAD_PHONE` | not a 10-digit Indian mobile |
| `NAME_TOO_LONG` | `user_name` > 25 chars |
| `WORKSHOP_TOO_LONG` | exceeds card width limit (TBC from final artwork) |
| `BAD_ADDRESS` | not `Locality, City`, or over length |
| `GENDER_UNSUPPORTED` | non-male this release |
| `UNKNOWN_BACKGROUND` / `UNKNOWN_OUTFIT` | value not in the plate mapping |
| `TEST_ROW` | matched the test heuristics or explicit list |
| `FETCH_FAILED` | photo unreachable |

The 100px floor is a sanity check for empty files, thumbnails and broken
uploads — **not a quality gate**. Output quality tracks input quality directly
and we have accepted that: a soft 270×390 crop produces a visibly softer video.
Expected, not a defect.

Stage-time errors are separate and retryable: `VENDOR_TIMEOUT`,
`VENDOR_REJECTED`, `BUDGET_EXHAUSTED` (terminal for the day), `ASSET_MISSING`,
`FFMPEG_FAILED`, `CHECK_FAILED` (logged, non-blocking).

---

## 13. Card renderer

**Implemented in** [`stages/media.py`](../src/castrol_pipeline/stages/media.py) — `render_card`, `card_payload`, `composite`. This is the ONE implementation; [`spikes/prototype.py`](../spikes/prototype.py) imports it rather than keeping a copy.

Deterministic Pillow render, transparent PNG, burned in by ffmpeg after video
generation.

```
Raju Shetty                        full name, bold hero line
Shetty Motors                      workshop, bold
Andheri, Mumbai | Mo. 9898989898   locality/city and the whatsapp number
```

**Template v2** (`card_template_version` in [`config.py`](../src/castrol_pipeline/config.py))
runs the panel to the **full frame width** — the client asked for the contact
details on one full-width line — while keeping v1's vertical rect, which is
the position that survived client review: `y 66.40% .. 81.13%`, top edge just
below the belt and clear of the hands. Identical across all six plates (every
plate shares framing and subject placement). Full video duration.

The rect is FIXED and the type adapts, which is the opposite of v1. A panel
that grew with its content changed size from job to job, and at full width
that reads as a different template rather than as a longer address. Content
that does not fit is scaled down as a block — every size and gap by the same
factor — so the proportions hold too. `MIN_SCALE` floors that at 0.55 and the
renderer logs `media.card_overflows` rather than shrinking past legibility.

Text fitting is rule-based and degrades predictably, as a unit per field. The
contact block goes: one line → address and phone on separate lines → address
wrapped at the comma that best balances the two lines by RENDERED WIDTH. That
choice is made once at nominal size, before any vertical scaling, because
wrapping and then scaling keeps the card in proportion where a shrunk font
pulling the address back onto one line would give a full-width line of tiny
type. Overflow is caught at intake by the length rules above, so the
renderer's fallback should rarely fire — if it fires often, that is a signal
the intake limits are wrong.

Bumping `card_template_version` puts the change into the composite
`input_hash`, so every open job re-renders and re-burns. That is free: the
composite stage is local ffmpeg and touches no vendor.

---

## 14. Checks

**Implemented in** [`stages/real.py`](../src/castrol_pipeline/stages/real.py) → `ChecksStage`. Results are written to the `checks` table by `orchestrator._persist_result` regardless of outcome.

Run on the final mp4, written to `checks` regardless of outcome. Logged, not
blocking, this release.

| Check | Why |
|---|---|
| card text OCR match | the deterministic path is still worth verifying end-to-end |
| chest mark template match | re-rendered per job on a varying torso — brand risk |
| sleeve + cap mark present | same, across both generative passes |
| face present across sampled frames | catches collapsed generations |
| subject geometry vs plate | card position depends on scale being preserved |
| hand skin tone vs face | the most visible tell after the face itself |
| audio/video duration delta | lipsync desync detector |
| file integrity, resolution, duration, size | cheap, catches truncated writes |

---

## 15. Observability

**Implemented in** [`common/logging.py`](../src/castrol_pipeline/common/logging.py) (structlog to stdout) and [`common/events.py`](../src/castrol_pipeline/common/events.py) (durable `job_events`, plus the credential scrubber). The table and the `job_costs` view are in [`0004_runtime_observability.sql`](../supabase/migrations/0004_runtime_observability.sql). Read them with `castrol events` / `castrol costs` / `castrol show`, all in [`cli.py`](../src/castrol_pipeline/cli.py).

`structlog`, JSON to stdout, `job_id` bound on every line inside a job context.
Every vendor call logs vendor, model id, task id, latency and outcome.

The batch summary row in `batches` is the single thing to look at each morning:
pulled / new / rejected / created / completed / failed, plus per-stage failure
counts. A systemic overnight failure shows up as zero completions rather than
as silence.

No alerting this release, by decision.

---

## 16. Environments and deployment

**Implemented in** [`config.py`](../src/castrol_pipeline/config.py); every variable is documented in [`.env.example`](../.env.example).

| Piece | Where |
|---|---|
| workers, poller, cron | AWS EC2 (single instance to start) |
| database | Supabase Postgres |
| object storage | AWS S3, private |
| delivery links | CDN in front of S3 |
| admin panel | Vercel |
| AI providers | apimart gateway |

**All of these are on a dedicated set of accounts, separate from the usual
BeHooked ones** — different GitHub, AWS, Supabase and Vercel logins. Git
pushes go through the `github-castrolinfra` SSH alias; the repo's local
`user.email` is set accordingly so commits are not attributed to the personal
account.

Migrations are forward-only numbered SQL applied in order. No down migrations —
rolling a schema back on a live batch is worse than fixing forward.

---

## 17. Security and data protection

**Enforced in** the RLS statements at the end of each migration, [`common/events.py`](../src/castrol_pipeline/common/events.py) `_scrub()` (no credentials or presigned URLs in the audit trail), and [`.gitignore`](../.gitignore) (no mechanic photos in git).

Face photos joinable to phone numbers is personal data under DPDP. Practically:

- S3 private, no public-read, ever
- delivery links unguessable and lifecycle-expired at 180 days
- Supabase **secret key** (`sb_secret_…`) server-side only; RLS deny-all so a
  leaked publishable key reads nothing. We use the new API keys, not the legacy
  `anon` / `service_role` JWTs — those are deprecated by end of 2026 and are not
  issued to projects created after 01 Nov 2025, so this project never had them.
  The `service_role` *Postgres role* is unaffected and still what a secret key
  authorizes as
- the pipeline processes use no API key at all — they connect to Postgres
  directly, so the only Supabase credential outside the admin panel is
  `SUPABASE_DB_URL`
- no personal data in log lines beyond `job_id` and `phone_e164`
- spike inputs and outputs are gitignored — real mechanic photos never enter git

---

## 18. Build order

Phase 0 exists to kill assumptions cheaply. Do not start Phase 1 without an
end-to-end video a human would accept.

| Phase | Deliverable | Done when |
|---|---|---|
| **0** foundation | repo, config, migrations, logging, budget function | migrations applied to the real Supabase project; `reserve_vendor_call` denies past the cap |
| **0** spikes | 0.1–0.6 per PROJECT_PLAN §12 | one hand-assembled video that passes human review |
| **1.1** | env/secrets, structured logging, budget wired | a stage cannot reach a vendor except through `budget.py` |
| **1.2** intake | export client, validation, dedupe, media fetch | a real day pulled and reconciled against the export by hand, standalone |
| **1.3** prep | script fill, normalisation, plate selection | pure functions, unit tested, zero I/O |
| **1.4** orchestration | state machine, claiming, retries, idempotency | full batch drained with **stub stages** that sleep and return fixtures |
| **1.5** generation | A, B, C, C2 behind the Stage protocol | first paid batch; vendor swap is a config change |
| **1.6** composite + checks | card renderer, ffmpeg burn-in, checks | card position verified on all 6 plates |
| **1.7** publish + deliver | S3, CDN, lifecycle, webhook client | link live, webhook 200 recorded in `deliveries` |
| **1.8** batch runner | nightly entrypoint, summary, resume | killed mid-batch and restarted without duplicate spend |
| **2** panel | Vercel read-only over Supabase | failures visible by stage and error code |

1.1 → 1.2 → 1.3 need no vendor at all and should be verified against real
client data first. 1.4 is provable with stubs. **Only 1.5 spends money**, and
by then everything around it is known good.

Plate generation (3 missing Uniform-2 plates) and card artwork are parallel
tracks, not blockers for 1.1–1.4.

---

## 19. Open technical questions

Blocking, in order:

1. ~~**TTS provider for stage A.**~~ **Decided: Cartesia, direct API**, with
   the voice created by hand in the Cartesia dashboard and referenced by id.
   This is a deliberate exception to "apimart + kie only" — that intersection
   has no voice-cloning Hindi lane. No cloning call ships in the pipeline. See
   §10 stage A.

2. ~~**Script runtime vs model max input duration.**~~ **Answered.**
   `kling-avatar-v2` has completed at 39s via kie and 60s via fal; the v1
   sibling has reached 113s. The ~80-word script at 30–40s is comfortably
   inside proven range and **does not need rewriting**. The real ceiling is
   *bytes, not seconds* — see invariant 11.

3. **Geometry / scale drift under a fixed-pixel card.** Still open, and there
   is no prior art to lean on: nothing in the existing backend holds a subject
   at a fixed pixel scale across an image edit — every path there is "generate
   a good-looking frame", never "preserve a pixel-locked region". Needs the
   plate dimensions and the card's pixel rect before it can even be reasoned
   about, and then a real spike.

4. **Timestamp format.** `03-09-2026 14:35` — confirm dd-MM-yyyy with the
   client before the first real pull.
3. **Export API auth.** Header scheme still pending from the client.
4. **`outfit` / `background` enum values.** `Castrol T-shirt` and `SUV`
   confirmed; the rest unknown, and an unmapped value is a rejected row.
5. **Card width limits.** Needed to set the `workshop_name` / `address` intake
   limits, which are currently TBC.
6. **`has_mechanic_id` semantics.** Reads FALSE while `mechanic_id` is
   populated. Confirm before the panel displays it as the mechanic's own ID.
7. **CDN choice** in front of S3 — Cloudflare vs CloudFront. Only matters if
   cryptographic expiry is required rather than unguessability.
