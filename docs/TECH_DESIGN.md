# Castrol MAGNATEC video pipeline — technical design

**Status:** Built and deployed (2026-09-15). Every stage implemented, migrations
0001–0014 applied, the worker running the CI-built container on EC2 behind a
systemd timer that is **not yet armed**. `DELIVERY_ENABLED` is still false.
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
generation. Everything is batch, **twice a day** — 00:00 and 12:00 IST.

---

## 2. System shape

**One process, run twice a day.** This section used to describe four
long-running processes — `intake`, `worker`, `poller`, `reporter` — and that is
not how it was built. They are four *phases* of one command,
[`cycle.py`](../src/castrol_pipeline/cycle.py), fired by a systemd timer at
00:00 and 12:00 IST and the only thing the server executes. A `oneshot` unit
that exits is what makes an interrupted run harmless: readiness is recomputed
from `stage_runs`, so the next cycle resumes without remembering anything.

| Phase of a cycle | In `cycle.py` | Job |
|---|---|---|
| lock | `db.advisory_lock` | a noon run landing on a still-rendering midnight run logs `cycle.skipped` and exits 0 rather than doubling vendor load |
| intake | `run_intake` over `intake_window` | pull `[today−1, today+1]` on the CLIENT's calendar → validate → land photos in S3 → insert submissions + jobs |
| repair | `_repair_orphans` | finish the rows a crash left half-created — a submission with no job, a job with no photo (invariant 33) |
| reopen | `_reopen_suppressed_deliveries` | re-deliver jobs that completed while `DELIVERY_ENABLED` was false (invariant 32) |
| schedule | `_schedule_all` | enqueue every job that is not `completed` or `cancelled` |
| work + poll | `drain_stage` / `poll_once` / `reap` | claim `stage_runs`, execute a stage, release; reconcile in-flight vendor tasks; sleep and repeat until nothing is queued and nothing is in flight |
| stop | the deadline | 8 hours, inside the 12-hour gap; exits 1 with work outstanding, losing nothing |

Concurrency is still per-stage and claiming is still `FOR UPDATE SKIP LOCKED`,
so more workers remains the answer if composite ever binds — the design below
is unchanged, it is the process count that was never four.

The admin panel (Vercel) reads the same Postgres and writes exactly one table,
`job_reports` (client review notes). It is not in the critical path and cannot
be.

**Why Postgres-as-queue and not SQS/Redis:** the state has to be inspectable and
resumable anyway, the volume is tens of rows a cycle — observed ~40 a day —
and `FOR UPDATE SKIP LOCKED` is sufficient. A second piece of infrastructure would buy nothing and
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
  cycle.py             the unattended run: lock, window, orphan repair, wait loop
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
    vendors.py         image / video / voice HTTP clients, submit + poll
    media.py           ffprobe, mp3, the Pillow card, the ffmpeg composite
scripts/
  apply_migration.py   the write path for migrations — one file per invocation,
                       DDL and its ledger row in a single transaction
supabase/migrations/   numbered SQL, forward-only, no down migrations
infra/                 AWS setup you run by hand (lifecycle rules, bucket posture)
deploy/                the systemd units and the EC2 runbook
panel/                 the Next.js admin panel on Vercel — client-facing, read-only
plates/                the six approved plate PNGs
uniform/               the flat uniform shots — the image edit's third input
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
| `audio` (A) | `stages/real.py` → `AudioStage`, via `stages/vendors.py:voice_tts` | voice provider | $0.00005/char |
| `image` (B) | `stages/real.py` → `ImageStage`, via `vendors.py:image_submit/_poll` | image provider | $0.014 |
| `video` (C) | `stages/real.py` → `VideoStage`, via `vendors.py:video_submit/_poll` | video provider | $0.036/s |
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
[`0003`](../supabase/migrations/0003_cartesia_tts_no_repair.sql) (voice lane, no repair
pass), [`0004`](../supabase/migrations/0004_runtime_observability.sql) (per-attempt
cost, `assets.cdn_url`, `job_events`, the `job_costs` view),
[`0005`](../supabase/migrations/0005_admin_review_and_export_copy.sql) (`job_reports`,
the raw export copy),
[`0006`](../supabase/migrations/0006_real_export_schema.sql) (the REAL export columns —
`card_phone_e164`, `mechanic_id_verified`, the client's own row `id`),
[`0007`](../supabase/migrations/0007_usage_views_for_the_panel.sql) and
[`0008`](../supabase/migrations/0008_job_usage_video_url.sql) (`job_usage` /
`daily_usage`, duration-only views the panel reads),
[`0009`](../supabase/migrations/0009_plate_uniform_reference.sql)
(`plates.uniform_ref_key`),
[`0010`](../supabase/migrations/0010_bill_on_the_render_not_the_trim.sql) (bill on the
render, not the trim) and
[`0014`](../supabase/migrations/0014_ceil_the_billed_second.sql) (ceil that
render to a whole second per row, because the provider rounds up and charges
per job),
[`0011`](../supabase/migrations/0011_raise_the_daily_cost_caps.sql) /
[`0012`](../supabase/migrations/0012_raise_the_call_caps_to_match.sql) (the daily caps)
and [`0013`](../supabase/migrations/0013_refunded_runs.sql) (`stage_runs.refunded`).
0001–0014 are applied.

Notes on the decisions that are not obvious from the DDL:

**The two phone columns are two different numbers.** `phone_e164` is the
export's `whatsapp_number` — the DELIVERY key, what we POST back as `phone`,
never printed and never spoken. `card_phone_e164` is `mechanic_phone_number`,
the contact number the CARD prints. Confirmed by the client 2026-09-08; they
are not interchangeable and must not be collapsed back into one column.

**`has_mechanic_id` is derived, not supplied.** The export sends
`mechanic_id_verified`, a string (`VERIFIED` / `NOT VERIFIED`), which `0006`
added; the original boolean is kept and populated as `== 'VERIFIED'` by
`intake/runner.py`. Neither is joined on.

**`job_id` (uuid) is the primary key of the system.** `mechanic_id` from the
client is stored as an opaque string, carried through, and never joined on —
it is inconsistently formatted (`MECH|8932442`, bare integers of varying
length), and the verification flag can disagree with it. The export's own `id`
is the unique identifier in the feed, and intake requires it.
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
| image | plate sha256 + source photo sha256 + uniform-ref sha256 (`""` if none) + prompt version + `IMAGE_EDIT_MODEL_ID` |
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

**Reserved is not billed, and `0013` is where the two part company.** Every
failed vendor job refunds its credits — confirmed against the provider
dashboard 2026-09-15, with no exception for a rejection or for a task that
timed out on our side. So `mark_failed` sets `stage_runs.refunded` on any
terminal failure that carried a cost, and `job_costs.cost_usd` counts only
unrefunded attempts, surfacing the rest as `refunded_usd`. `vendor_usage` is
deliberately NOT adjusted: its reservation is what bounds a runaway loop, and a
budget that gives money back on failure is one a retry loop can walk straight
through. See invariant 24 — including the note that the reason originally
written beside it was wrong for this vendor.

Every outbound paid call goes through `common/budget.py`, which calls
`reserve_vendor_call(vendor, cost_usd, seconds)` and refuses the call on
`false`.

**Cap dollars, not calls.** One 35s video costs ~$1.51, and the avatar step is
**93–96% of it** — priced per output second, not per call:

| Step | Model / lane | Cost | Share |
|---|---|---:|---:|
| Image edit | `gpt-image-2-max`, image provider, 2K | $0.0140 | 1.3% |
| TTS | ~450 chars, per character | $0.0226 | 2.1% |
| Avatar | `kling-avatar-v2` standard, video provider, 29.4s | **$1.0567** | **96.7%** |
| | **all-in per video** | **$1.093** | |
| | `pro` variant instead | $2.150 total | avatar = 98% |

Measured over 11 real renders on 2026-09-15, not modelled. The earlier row
(`$0.012` / `$0.100` billed as a 1k block / `$1.400` at $0.04/s) predated both
the rate correction in `cf00e4a` and the discovery that the voice provider
bills per character with no block rounding.

A call-count cap bounds volume but bounds *spend* only within ~3× (script
length) × ~2× (standard vs pro), which is why `vendor_limits` carries both.
That reasoning stands, and `0011`/`0012` (2026-09-15) reasserted it after a
brief inversion. `0011` raised `daily_cost_cap_usd` to $5000 on `kie_video` and
$500 on the other two but left the call caps at 200/600/600 — which made THOSE
the ceiling (~$211/day on the video lane) while the number anyone would read
said $5000.
`0012` lifted the call caps to 5000 / 40000 / 25000 so the cost cap trips first
for every vendor again:

| vendor | cost cap | call cap | calls when cost trips | binds on |
|---|---|---|---|---|
| `kie_video` | $5000 | 5 000 | ~4 732 | **cost** |
| `apimart_image` | $500 | 40 000 | ~35 714 | **cost** |
| `tts` | $500 | 25 000 | ~22 124 | **cost** |

The two cheap vendors had to move as well, and not because they were given a
budget: every video costs one image call and one TTS call, so a 600-call cap on
either would have halted the pipeline at 600 videos — under the video lane's
~4 732 — and
relocated the binding constraint to stage A or stage B without announcing it.
A cap is only a guard if it is the one you think it is.

To tighten spend, move the COST cap and check the call cap still sits above
it.

The cap day is **IST** (`now() at time zone 'Asia/Kolkata'` inside
`reserve_vendor_call`), so both the 00:00 and 12:00 IST cycles spend from one
bucket.

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
on either gateway. A network-level retry of a submit creates a second provider
job and a second charge, and the budget function cannot see it — it reserved
once. Dedupe before the HTTP call, and on an ambiguous submit *reconcile*
rather than resubmit.

The same reasoning bounds `STAGE_CLAIM_TIMEOUT_S`: if the reaper requeues a row
whose provider call is still running, the retry pays twice. It must exceed the
longest a worker can legitimately hold a claim. Only `is_async = False` stages
hold one — async stages submit, store `vendor_task_id`, and release to
`running`, which the reaper does not touch. **The image stage is currently
`is_async = False` and should become async when the real stage lands**, since
The image gateway is poll-only with a 2700s ceiling and an observed 644s worst case.

These are two different clocks and they are often confused:

| | bounds | applies to | value |
|---|---|---|---|
| `STAGE_CLAIM_TIMEOUT_S` | a dead WORKER | rows in `claimed` | 900s (15m) |
| `VENDOR_TASK_TIMEOUT_S` | a dead VENDOR TASK | rows in `running` | 7200s (2h) |

The claim timeout is the stuck-worker reaper: a process killed by OOM, SIGKILL
or an instance stop leaves its row `claimed` forever, and nothing else would
ever pick it up. Fifteen minutes is generous — nothing here takes more than a
second between claiming and submitting.

The vendor timeout was raised 90m → 2h on 2026-09-15. It errs long on purpose,
and for the same double-charge reason: cost is recorded at SUBMIT (invariant
24), so a task failed early has already been paid for and its retry pays a
second time. Waiting on a genuinely dead task costs wall clock only, and the
cycle deadline (8h) is the real backstop.

---

## 9. S3 layout

**Implemented in** [`common/s3.py`](../src/castrol_pipeline/common/s3.py) — key builders, both backends, presigning, `cdn_url`. Lifecycle rules are [`infra/s3-lifecycle.json`](../infra/s3-lifecycle.json), applied by hand per [`infra/README.md`](../infra/README.md). Key rules are pinned by [`tests/test_storage_keys.py`](../tests/test_storage_keys.py).

```
s3://<bucket>/castrol/
  plates/<uniform>_<bg>/<sha256>.png       frozen, approved
  uniforms/<uniform>/<sha256>.png          garment detail reference, optional
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
**The voice provider, direct API.** The one deliberate exception to "the two
gateways only",
taken because that intersection has no voice-cloning Hindi lane at all. Hindi
and Gujarati are both prod-verified on this provider with a cloned voice.

The voice is **created by hand in the provider's dashboard** and referenced by
id. There is no cloning call in the pipeline — no `/voices/clone`, no
per-render clone latency, no voice lifecycle to manage. `TTS_VOICE_ID` is
config, pinned like a model id.

```
POST https://api.cartesia.ai/tts/bytes
Authorization: Bearer $TTS_API_KEY
Cartesia-Version: 2026-05-11
{ "model_id": "sonic-3.6",
  "transcript": "<filled script>",
  "voice": { "mode": "id", "id": "<dashboard voice id>" },
  "output_format": { ... } }
→ raw audio bytes. Synchronous. No task id, no polling, no duration.
```

Two things this stage owns beyond the call, both mandatory:

1. **Emit MP3.** The avatar model's `"Audio size is too large"` is a byte
   limit, not a duration limit, and every observed failure was a WAV.
   The provider's default `pcm_f32le` @44.1kHz is ~176 KB/s — 40s is ~7 MB
   against
   ~640 KB as MP3. Request an MP3 container if it will emit one;
   otherwise transcode before handing off. Either way stage C never sees a WAV.
2. **Probe the duration with ffmpeg and record it.** The provider returns no
   duration, and stage C bills per output second — this probe is a billing
   input, not a convenience. Fail closed to the cap, never to zero.

**Billing is per CHARACTER with no block rounding** — 1 credit per character
at 100K credits per $5, i.e. $0.00005/char. This paragraph used to say it ceils
per 1000 characters with a minimum of 1, and priced the stage at ~$0.10 and ~7%
of the video. Measured over 11 real runs it is **$0.0226**, about **2%** —
`USD_PER_TTS_CHAR` in `common/budget.py` is the rate that reserves.

**Language note:** `language_code` was null on every verified Hindi run, and
the clone call's `language` field does not appear to gate synthesis language.
Do not assume it needs setting; test before adding it.

### B — image
Person replacement on the frozen plate. Prompt is structured
Change / Preserve / Constrain (see PROJECT_PLAN §4, "Person replacement").
Geometry preservation is not cosmetic: the card sits at a fixed pixel position,
so subject scale drift puts the card over the mechanic's hands.

The plate is a **pose and composition reference, not a frozen asset** — the
chest mark is re-rendered on a torso whose shape varies per person and cannot
be composited.

Since the 2026-09-11 artwork there is only the chest mark to protect: the new
uniforms have **no cap and no sleeve logo**, and the chest panel reads
`Castrol` alone rather than `Castrol MAGNATEC` on two lines. `IMAGE_PROMPT`'s
preserve clause used to name all four marks, which asked the model to keep
branding the garment no longer has — and it duly invented a garbled sleeve
patch. `image_prompt_version` is therefore **v2**, and unlike the avatar prompt
(invariant 30) this one is hashed BY VERSION, so it must be bumped by hand or
open jobs skip stage B and ship the old inventory.

The single-word mark also survives the avatar model's per-frame redraw, which
the two-line one never did: nine of nine renders on 2026-09-14 read a clean
`Castrol` where every earlier render smeared `Castrol MAGNAT..`. Months of that
was blamed on hand motion and on tier resolution. It was the artwork.

**The uniform reference** (migration `0009`) is the answer to that same fact. If
the garment is going to be redrawn on every job, the model should be copying it
from a flat shot on a plain background rather than reconstructing it off a
figure it is simultaneously changing. It is a third `image_urls` entry —
`[plate, mechanic, uniform]`, and the prompt addresses them by ordinal, so the
order is load-bearing — plus one extra prompt clause, both switched on by the
job's plate row having a `uniform_ref_key`. Three references stays well inside
the ~4 cap, and none of them is generated (invariant 17): this is client
artwork, exactly like the plate.

It stays ONE generation and ONE change, and the clause is written to keep it
that way. The uniform is something that must not change; the third image only
says what it already looks like. Phrased as an instruction - "reproduce these
details" - it contradicts `Make EXACTLY ONE change` two paragraphs above, so it
opens by excluding itself from that change instead.

The rest of the clause is defensive about geometry. A second image of the same
garment, framed differently, is the most direct route to the reframe invariant 6
exists to prevent, so the clause itself scopes the third image to fabric, seams,
collar and printed marks, and says fit, size and position come from the first
image.
[`tests/test_image_prompt.py`](../tests/test_image_prompt.py) pins that the
geometry constraints survive in both prompts and that the two-image prompt
never names a third image.

The reference hangs off the plate ROW rather than a `uniform_id` lookup, so
invariant 31 covers it: `jobs.plate_id` stays the one honest record of the
artwork a video was built from, reference included.

### C — video
The `prompt` field is REQUIRED on the video provider (max 5000 chars) and is
not decorative:
it steers expression, head movement and hand gesture. `AVATAR_PROMPT` in
[`stages/vendors.py`](../src/castrol_pipeline/stages/vendors.py) follows the
model's documented shape — subject, expression, motion, style preservation, in
a few sentences — and carries constraints that come from our own pipeline
rather than from the model. The largest is that it asks for **no hand
gestures**: hands stay at waist level exactly where the source image has them,
moving only with calm, slow, natural motion. `calm` and `slow` are both
required and say different things — slow bounds per-frame displacement, which
is the mechanical cause of smear; calm bounds intent. Neither means frozen,
which reads as a still photograph with a talking head pasted on.

That is the conclusion of three paid revisions, each of which found a different
way for this model to render moving hands badly - r1 looped and smeared one
gesture; r2 named three at "chest height ... clear of the chest logo", a
position and an exclusion pointing at the same region, and both hands came back
clawed across the chest panel; r3 bounded the band on both sides and dropped the
finger-counting, which produced clean open palms that still rose to chest level
for no return.

It also asks the hands to stay apart and clear of one another - the client's
words after reviewing a batch. Hands that meet are where this model renders
fingers worst, because it has to invent an occlusion. Seven of nine renders on
2026-09-14 held them apart for the whole take; two converged at the belt near
the end. What carries the video is the face, which is the part this model has
always done well: the inherited "." default gave correct lipsync and natural
head motion, and only the hands were ever the problem.

**The card used to hide all of this. It no longer does.** The argument was that
hands resting at ~71-78% of frame height sat behind an opaque overlay across
66-82%, so hand failures were invisible rather than merely less likely. Measured
on real renders, the hands are at **60-70%** - so the overlay's top edge cut
across the fingers and they read as severed by the panel. The card moved down to
72.27% on 2026-09-15 and the hands are on screen throughout. The prompt is now
the only thing keeping them presentable.

**Output resolution is set by the TIER, and by nothing else.**
`kling/ai-avatar-standard` returns 720x1280 whatever it is fed;
`kling/ai-avatar-pro` returns 1072x1920. There is no resolution or fps field on
either endpoint - the gateway's parameter mapping is a strict allowlist and
drops anything unmapped without erroring - so `VIDEO_MODEL_ID` is the whole
control. Plate resolution above the tier's output buys nothing downstream.

Pro was **ruled out on 2026-09-10** as too expensive and **reinstated on
2026-09-12** when the client asked for 1080p. Do not cite the old decision as
standing. The mistake in between is worth recording: "the provider outputs
720x1280
regardless of input resolution" was measured on a *standard* render and
generalised into a limit of the model. It is a limit of the tier, and that error
sent five prompt revisions chasing a chest-logo smear that resolution was never
going to fix.

Nor, as it turned out, was resolution the cause. The `MAGNATEC` mark stopped
smearing when the **artwork** changed: the new uniforms carry a single-word
`Castrol` chest mark, which survives the avatar model's per-frame redraw where
the two-line one never did. Nine of nine renders on 2026-09-14 came back clean.

`video_is_pro` is derived from `VIDEO_MODEL_ID` rather than set by a separate
flag, so the reservation always follows what was actually submitted. There was a
standalone `VIDEO_USE_PRO`, and setting either without the other either
over-reserved or - worse - under-reserved by 2x on the only expensive step, in
silence. The prompt text is hashed directly, so editing it regenerates rather
than silently skipping.

`kling-avatar-v2`, on the video provider. Async submit, `vendor_task_id` stored,
poller
reconciles. The bottleneck, and 93–96% of the money.

Latency is measured, not guessed: ~256s at 16s of audio, ~607s at 30s,
**~1191s (~20 min) at 39s**. Budget 8–20 minutes for a 30–40s render and size
every timeout above it.

The response shape has two traps worth restating: HTTP 200 with `code != 200`
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

### Inbound — export pull

```
GET {CLIENT_EXPORT_URL}?from=YYYY-MM-DD&to=YYYY-MM-DD
Header: apikey: $CLIENT_EXPORT_API_KEY
→ CSV, 18 columns, UTF-8 with a BOM. Rate limit 100 / 900s.
```

**The response is CSV, not JSON**, and the body carries a **UTF-8 BOM** — so
`export_client.py` decodes `utf-8-sig`. As plain utf-8 the first header becomes
`﻿id` and `id`, the only unique identifier in the feed, silently reads as
missing while the other 17 columns parse perfectly. Pinned by
[`tests/test_export_csv.py`](../tests/test_export_csv.py).

The real header, in order:

```
id, whatsapp_number, user_name, workshop_name, address, gender,
mechanic_id_verified, mechanic_id, mechanic_phone_number, background,
outfit, image_url, image_mime_type, image_validation_status,
image_rekognition_status, status, createdAt, updatedAt
```

An unexpected column is carried through to the raw copy untouched rather than
filtered — `KNOWN_COLUMNS` exists to NOTICE a schema change, not to enforce one.

The pull is **not idempotent**: overlapping windows re-return rows and late
submissions land in later windows. Dedupe is ours, and it has **two** anchors:
`submission_hash = sha256(media_key + phone_e164)`, where `media_key` is the
blob path with the query string stripped, and the client's own row `id`. The
second is what stops an overlapping window turning a re-issued media url into a
UNIQUE violation that fails the whole batch. Timestamps are ISO 8601
(`2026-09-07T10:13:49.681Z`, with and without millis) and the format is pinned
in `EXPORT_TIMESTAMP_FORMAT`, never inferred; the raw string is stored too, so
a wrong format can be reparsed without re-pulling.

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
| `FACE_COUNT_NOT_1` | `image_rekognition_status != FACE_DETECTED`. The name outlived its rule: **the real export carries no `image_face_count`**, only a status string, so the group-photo case this code was named for is no longer detectable at intake and falls to the stage B checks. [`tests/test_intake.py`](../tests/test_intake.py) asserts that gap deliberately |
| `BAD_MIME` | magic bytes not a recognised image type |
| `IMAGE_TOO_SMALL` | short edge < 100px |
| `BAD_PHONE` | `whatsapp_number` or `mechanic_phone_number` not a 10-digit Indian mobile. Both are checked: the client states the card number is never empty, and this is that promise encoded as a check rather than an assumption |
| `NAME_TOO_LONG` | `user_name` > `MAX_NAME_CHARS` = 30 (raised from 25 on 2026-09-15, after a real row hit 23 — 92% of the old bound, and this code is terminal) |
| `WORKSHOP_TOO_LONG` | `workshop_name` > `MAX_WORKSHOP_CHARS` = 30 |
| `BAD_ADDRESS` | empty, or over `MAX_ADDRESS_CHARS` = 90. **Not a shape rule:** the address is free text of any form (client, 2026-09-09), so 90 is a sanity bound that catches a pasted paragraph. It used to require exactly `Locality, City`, which rejected a one-word `Worli` |
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
Andheri, Mumbai | Mo. 9898989898   the address, and the CONTACT number
```

**That number is `card_phone_e164` (`mechanic_phone_number`), never
`phone_e164`.** `_card_fields` read the wrong one until 2026-09-15, so every
card printed the mechanic's WhatsApp number burned into a video that gets
shared around. The column existed and intake populated it correctly; only the
renderer was wrong, which is why nothing looked broken. It falls back to
`phone_e164` only when the card number is null — the `seed-job` case, where a
single `--phone` supplies both. Pinned by
[`tests/test_card_fields.py`](../tests/test_card_fields.py). That fix needed no
`card_template_version` bump: the value sits inside `media.card_payload`, which
is already in the composite `input_hash`, so a job whose printed number really
changes re-burns on its own.

The first line of the address is what the card prints in full; the voice says
only its last segment (`prep/normalise.py:spoken_place_from`), because Indian
addresses run most-specific to least and reading it all aloud puts a hospital
landmark in a 30-second ad.

**Template v2** (`card_template_version` in [`config.py`](../src/castrol_pipeline/config.py))
runs the panel to the **full frame width** — the client asked for the contact
details on one full-width line — while keeping v1's vertical rect. Identical
across all six plates (every plate shares framing and subject placement). Full
video duration.

**v3** is not a card change at all: the composite now trims the video to its own
audio stream, cutting the silent tail `kling-avatar-v2` leaves after the speech
ends. The version covers the composite OUTPUT, not just the artwork, so anything
changing what composite emits bumps it.

**v4 (2026-09-15) slides the same rect down to `y 72.27% .. 87.00%`** — 5.87% of
frame height lower, nothing else changed. v1's position (`66.40% .. 81.13%`,
"top edge just below the belt and clear of the hands") was chosen against a
plate whose subject stood with folded arms. The avatar prompt now parks the
hands at belt height and keeps them there, at a measured 60-70% of frame height,
which is exactly where that top edge sat: it cut across the fingers. The band
now starts below them.

The rect is FIXED and the type adapts, which is the opposite of v1. A panel
that grew with its content changed size from job to job, and at full width
that reads as a different template rather than as a longer address. Content
that does not fit is scaled down as a block — every size and gap by the same
factor — so the proportions hold too. `MIN_SCALE` floors that at 0.55 and the
renderer logs `media.card_overflows` rather than shrinking past legibility.

A content-driven height was built and rejected on 2026-09-15. It does fix the
one real cost of the fixed rect — two mechanics in a batch getting visibly
different type because one address wrapped — but a band that changes size
between jobs is the louder fault. Declined, not overlooked.

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
blocking — a deliberate release decision, not an oversight: there are not yet
enough real outputs to set a threshold that would not reject good videos, and
the results are recorded so that threshold can be set from data.

**Three checks are implemented.** This table used to list eight, which read as
a description of what runs; the other five were never built.

| Check | Rule | Why |
|---|---|---|
| `duration_matches_audio` | `\|final_ms − audio_ms\| ≤ 1500` | the avatar model is driven by the audio, so a final video that is not the length of the audio means a truncated render |
| `is_vertical_9x16` | `height > width` | catches a plate or a re-encode that lost its orientation |
| `bitrate_plausible` | `bytes / seconds > 200 KB/s` | a composite that lost most of its bitrate means the re-encode fell back to defaults |

**Not built, and the risk table should not claim otherwise:** card text OCR
match, chest-mark template match, face present across sampled frames, subject
geometry vs plate, hand skin tone vs face. The first two are the brand-risk
checks named in `PROJECT_PLAN` §13; the geometry one is what spike 0.3 would
need. Until they exist the chest mark and the hands are protected by the
artwork and the prompt (§10 B, §10 C), reviewed by eye, and by nothing
automatic.

---

## 15. Observability

**Implemented in** [`common/logging.py`](../src/castrol_pipeline/common/logging.py) (structlog to stdout) and [`common/events.py`](../src/castrol_pipeline/common/events.py) (durable `job_events`, plus the credential scrubber). The table and the `job_costs` view are in [`0004_runtime_observability.sql`](../supabase/migrations/0004_runtime_observability.sql). Read them with `castrol events` / `castrol costs` / `castrol show`, all in [`cli.py`](../src/castrol_pipeline/cli.py).

`structlog`, JSON to stdout, `job_id` bound on every line inside a job context.
Every vendor call logs vendor, model id, task id, latency and outcome.

The batch summary row in `batches` is the single thing to look at each morning:
pulled / new / rejected / created / completed / failed, plus per-stage failure
counts (`castrol report`). A systemic overnight failure shows up as zero
completions rather than as silence.

**The cycle's two self-heals both log at warning level, on purpose.** A silent
self-heal is how a recurring crash stays invisible for a month, so
`_repair_orphans` records `cycle.orphan_job_created` /
`cycle.orphan_photo_restored` / `cycle.orphan_photo_failed` and
`_reopen_suppressed_deliveries` records `cycle.redelivering` — see invariants 33
and 32. Neither is an error; both mean something upstream was interrupted.

`job_costs` reports `cost_usd` net of refunds and `refunded_usd` beside it
([`0013`](../supabase/migrations/0013_refunded_runs.sql)), so the gap between
what was reserved and what was billed is auditable rather than invisible. Note
`castrol costs` does not yet select `refunded_usd`.

**The panel's `job_usage.video_seconds` is CEILED per row, in the view**
([`0014`](../supabase/migrations/0014_ceil_the_billed_second.sql)) — not rounded,
and not ceiled afterwards in the panel. Both of those under-recovered: `round(x, 1)`
can cross an integer boundary downward (27.04 → 27.0 → ceils to 27 where the
provider billed 28, a second SQL had already thrown away), and ceiling a SUM is
not the sum of the ceilings (nine renders billing 250s totalled 246s). The
provider charges per output second and rounds UP, per render, so the view has to
match that shape row by row. `format.ts` ceils as well and is now a no-op on
anything this view returns — kept as the guard for the day someone edits the
expression back.

No alerting this release, by decision.

---

## 16. Environments and deployment

**Implemented in** [`config.py`](../src/castrol_pipeline/config.py); every variable is documented in [`.env.example`](../.env.example).

| Piece | Where |
|---|---|
| workers, poller, cron | AWS EC2 — `i-0d7560cd333c94cde`, `t3.medium`, `ap-south-1a` |
| database | Supabase Postgres |
| object storage | AWS S3, private |
| delivery links | CDN in front of S3 |
| admin panel | Vercel — live at <https://castrol-pipeline-admin-panel.vercel.app> |
| AI providers | image edit gateway, avatar gateway, voice TTS (direct) |
| worker image | Docker Hub, `gethooked/castrol-video-pipeline`, built by CI |

The worker runs `castrol cycle` under a systemd timer at 00:00 and 12:00 IST,
as a container pulled from Docker Hub. The as-built record — resource ids, the
decisions taken while provisioning, and what was verified — is
[`EC2_DEPLOYMENT.md`](EC2_DEPLOYMENT.md); the runbook is
[`deploy/README.md`](../deploy/README.md).

**GitHub, Supabase, Vercel and the AI providers are dedicated logins, separate
from the usual BeHooked ones.** Git pushes go through the `github-castrolinfra`
SSH alias; the repo's local `user.email` is set accordingly so commits are not
attributed to the personal account.

**AWS is the exception**, and this section used to claim otherwise.
`castrol-local` lives in account `872515254882` — the shared BeHooked account,
running `behooked-studio-backend-prod`, `hooked-micro-apps`, `hooked-nodeflow`
and `orchestrator-prod` in the same default VPC. What is dedicated is the IAM
user and the bucket, and that is the whole of the separation; anything created
here is created beside four production services. Corrected 2026-09-15.

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

**All of the below has shipped**, and the table is kept as the record of what
each phase had to prove rather than as a plan. Phase 0 existed to kill
assumptions cheaply; the exit criterion — one end-to-end video a human would
accept — was met by [`spikes/prototype.py`](../spikes/prototype.py), which is
now permanent because the card has one implementation and the spike imports it.

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
| **2** panel | Vercel over Supabase, writing only `job_reports` | failures visible with a reason — NOT by stage or error code, which a client-facing surface must not show |

1.1 → 1.2 → 1.3 need no vendor at all and were verified against real client
data first. 1.4 is provable with stubs. **Only 1.5 spends money**, and by then
everything around it was known good.

The plate and card-artwork tracks are also closed: **all six plates exist**,
were replaced with new artwork on 2026-09-11 (1152x2048, a true 9:16) and
re-registered on 2026-09-15, and all six carry a uniform reference. The card is
at template v4. What remains open is not build order — see §19.

---

## 19. Open technical questions

**Actually open, in order:**

1. **Geometry / scale drift under a fixed-pixel card** — spike 0.3, and the one
   question with no prior art: nothing in the existing backend holds a subject
   at a fixed pixel scale across an image edit. Every path there is "generate a
   good-looking frame", never "preserve a pixel-locked region".

   What was missing when this was written is now known. The plates are
   **1152x2048**; the card rect is fixed at **`y 72.27% .. 87.00%`** (template
   v4); the hands sit at a measured **60–70%** of frame height across nine
   renders. What is still missing is a per-job measurement: no check compares
   subject geometry against the plate (§14), so drift would show up as a card
   over the hands, in a render somebody happens to look at.

2. **Throughput at volume** — the latency half of spike 0.2 is answered
   (~8–20 min per render, measured), the concurrency half is not. Observed
   volume is ~40 videos a day against `MAX_CONCURRENCY_VIDEO=10`, and a cycle
   waits rather than blocking, so this is not pressing. It becomes pressing the
   first time a batch outlasts the 8-hour deadline.

3. **No automated brand or geometry check.** §14 lists what is built; the
   chest-mark and hand checks the risk table in `PROJECT_PLAN` §13 names as
   mitigations do not exist. Either build them or stop calling them mitigations.

**Closed, with the answer, so they are not reopened by accident:**

- ~~**TTS provider for stage A.**~~ **the voice provider, direct API**, voice created by
  hand in the dashboard and referenced by id. A deliberate exception to
  "the two gateways only" — that intersection has no voice-cloning Hindi lane. No
  cloning call ships. See §10 A.
- ~~**Script runtime vs model max input duration.**~~ `kling-avatar-v2` has
  completed at 39s via the video provider and 60s elsewhere; the ~80-word script at 30–40s is
  comfortably inside proven range and **does not need rewriting**. The real
  ceiling is *bytes, not seconds* — invariant 11.
- ~~**Timestamp format.**~~ **ISO 8601** (`2026-09-07T10:13:49.681Z`, with and
  without millis), confirmed against a live pull 2026-09-08.
  `EXPORT_TIMESTAMP_FORMAT=iso8601` names the standard instead of restating a
  pattern, and is still never inferred. The earlier `03-09-2026 14:35` /
  dd-MM-vs-MM-dd worry belonged to the pre-CSV schema.
- ~~**Export API auth.**~~ An **`apikey` header**. Live since 2026-09-08.
- ~~**`outfit` / `background` enum values.**~~ Mapped in
  [`prep/plates.py`](../src/castrol_pipeline/prep/plates.py) against the
  client's own combination map (2026-09-09). Backgrounds 1|2|3 **are** the SUV,
  sedan and hatchback — the ids were always right, the lookup keys were not.
  Uniform ids are `u1_tshirt` / `u2_uniform`, the client's number and the
  client's word; `polo` / `half_shirt` were ours and one of the two values is
  literally a t-shirt. The client's phrasings are accepted as aliases, and an
  unmapped value is still a rejected row rather than a guess.
- ~~**Card width limits.**~~ Name ≤ 30, workshop ≤ 30, address ≤ 90 as a sanity
  bound — §12. The rect is fixed and the type scales to fit (invariant 27), so
  these bound what the renderer is asked to fit rather than expressing a layout.
- ~~**`has_mechanic_id` semantics.**~~ The export sends
  `mechanic_id_verified`, a string; the boolean is derived from it (§4). Neither
  is joined on and the panel does not display either as the mechanic's own ID —
  it searches on `mechanic_id` as opaque text.
- ~~**CDN choice.**~~ **CloudFront**, over an unguessable key, with no Origin
  Path — which is why the full key including `castrol/` must appear in the URL
  (invariant 20). Cryptographic expiry was not required; the 180-day lifecycle
  rule is what ends a link (invariant 22).
