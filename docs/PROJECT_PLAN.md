# Castrol MAGNATEC — Mechanic Promo Video Pipeline

**Status:** Built and deployed. The pipeline runs end to end against real
Supabase, S3 and CDN; nine jobs have completed for real. The worker is on EC2
behind a twice-daily systemd timer — **not yet armed**, and `DELIVERY_ENABLED`
is still false, so nothing has reached a mechanic. See
[`EC2_DEPLOYMENT.md`](EC2_DEPLOYMENT.md).
**Last updated:** 15 Sep 2026
**Supersedes:** WhatsApp Personalised Video Campaign plan (21 Aug 2026)

---

## 1. What changed since the last version

The previous plan assumed we build the WhatsApp bot: Meta Cloud API, webhook receiver, 24-hour window, template fallback, conversation state machine, consent capture, message status tracking.

**None of that is ours.** The client runs the WhatsApp side on Interakt (BSP) and has given us two HTTP contracts:

- an **export API** we pull mechanic submissions from
- a **delivery webhook** we POST the finished video URL to, keyed on phone number

Everything Meta-related is now out of scope. Consent is collected in the client's form. Delivery to the mechanic is the client's job.

**Our scope is a batch video factory:** pull rows → validate → generate → host → POST back.

This is a large scope reduction and should be written into the SOW explicitly, because "the WhatsApp bit" will otherwise drift back to us.

---

## 2. Deliverable

A vertical promo video per mechanic. Fixed Hindi script with three inserted variables, spoken in a fixed voice, over one of six pre-built garage scenes, with a personalisation card burned in.

| | |
|---|---|
| Script | Fixed, one version, Hindi/Hinglish |
| Voice | Fixed, male only (client supplied 2 refs, one to be chosen) |
| Scenes | 6 (2 uniforms × 3 backgrounds) |
| Runtime variables | Mechanic name, workshop name, location — in audio and on card |
| On-screen card | Name, workshop, location, phone number |
| Gender | Male only for this release |

---

## 3. Architecture

**Implemented in** [`orchestrator.py`](../src/castrol_pipeline/orchestrator.py) and [`stages/`](../src/castrol_pipeline/stages/). Repo layout with a file-by-file index: [`TECH_DESIGN.md` §3](TECH_DESIGN.md).

```
   Client export API  (daily pull, from/to date range)
            │
            ▼
   INTAKE ──── validate, normalise, dedupe, filter test rows
            │  reject code recorded; rows are never repaired
            ▼
   Supabase (job + stage state)
            │
     ┌──────┴──────┐
     ▼             ▼
  A: AUDIO      B: IMAGE
  TTS + voice   plate (frozen) + owner photo
  reference     → person replace
     │             │
     └──────┬──────┘
            ▼
   C: VIDEO   avatar / lipsync  ← bottleneck, async submit + poll
            │                     (there is NO repair pass — see §5)
            ▼
   D: COMPOSITE  burn personalisation card
            │
            ▼
   CHECKS   machine validation, log always
            │
            ▼
   S3 + CDN ──▶ POST client delivery webhook {phone, videoLink}
```

Infrastructure unchanged: AWS S3, Supabase Postgres, AWS EC2, GitHub,
Cloudflare/Vercel for the panel. Note the AWS account is the **shared** BeHooked
one — only the IAM user and the bucket are dedicated
([`EC2_DEPLOYMENT.md` §1](EC2_DEPLOYMENT.md)).

---

## 4. Assets

**Plate selection** is [`prep/plates.py`](../src/castrol_pipeline/prep/plates.py); the plate rows are seeded by [`0002_budget_and_seed.sql`](../supabase/migrations/0002_budget_and_seed.sql) and activated by [`seed.py:register_plate`](../src/castrol_pipeline/seed.py). S3 layout is [`common/s3.py`](../src/castrol_pipeline/common/s3.py).

### Plate matrix — 6 combinations

| Combo | `uniform_id` | Background |
|---|---|---|
| `plate_01` | `u1_tshirt` | `bg1_white_suv` — indoor garage, white SUV, red tool cart |
| `plate_02` | `u1_tshirt` | `bg2_dark_sedan` — indoor garage, dark sedan |
| `plate_03` | `u1_tshirt` | `bg3_hatchback_hood` — weathered garage, hatchback, open hood |
| `plate_04` | `u2_uniform` | `bg1_white_suv` |
| `plate_05` | `u2_uniform` | `bg2_dark_sedan` |
| `plate_06` | `u2_uniform` | `bg3_hatchback_hood` |

**All six exist.** The uniform ids are the client's number and the client's
word: `polo` / `half_shirt` were ours, and since one of the two garments is
literally a t-shirt, which way round they mapped was a coin flip. The
backgrounds 1|2|3 **are** the SUV, sedan and hatchback — the ids were always
right, only the lookup keys were wrong until 2026-09-09.

The artwork was **replaced on 2026-09-11** and re-registered on 2026-09-15, all
six at 1152x2048 (a true 9:16). Each combination now has three rows, one active
and two retired, and older jobs still point at the artwork they were built from
— replacing a combination RETIRES its row and inserts a new one rather than
editing in place, because `jobs.plate_id` is the only record of what a video was
actually made from (invariant 31).

Plates are generated once, human-approved once, and frozen. Uniform and car are never generated at runtime.

Each active plate row also carries a **uniform reference** (`uniform_ref_key`):
a plain-background shot of the garment, `uniform/u1_tshirt.png` on plates 01–03
and `uniform/u2_uniform.png` on 04–06, handed to the image edit as a third
input. See §4 "Person replacement".

### Brand marks per frame

**The 2026-09-11 artwork changed this, and the change was load-bearing.** The
uniforms now have **no cap and no sleeve logo**, and the chest panel reads
`Castrol` alone rather than `Castrol MAGNATEC` on two lines. So there are two
marks, not four:

1. Chest panel — `Castrol`  ← re-rendered per job, on a torso whose shape varies
2. Overhead banner — part of the frozen plate background

Only the first has to survive both generative passes. `IMAGE_PROMPT`'s preserve
clause used to name all four, which asked the model to keep branding the garment
no longer has — and it invented a garbled sleeve patch. `image_prompt_version`
went to **v2** for that reason, and to **v3** on 2026-09-16 for a second,
unrelated fault: bearded mechanics came back messy, because "carry over their
facial hair" names the feature without asking for its structure. The prompt now
names the structure (outline, jawline edge, length, density, patchiness, grey)
and blocks smoothing and tidying.

The single-word mark also turned out to be why the smearing stopped: nine of
nine renders on 2026-09-14 read a clean `Castrol`, where every earlier render
smeared `Castrol MAGNAT..`. Months of that was blamed on hand motion and on
tier resolution.

Two things the old artwork bought are gone with the cap: it no longer
standardises the head silhouette, and a tightly-cropped source photo with the
top of the skull cut off now needs the model to invent hair.

### Personalisation card

Per the client draft, the mechanic's details appear as a **lower-third card composited over the video** — not as text rendered into the garage scene. The garage banner stays fixed Castrol branding.

This removes generative text rendering from the pipeline entirely. Card is drawn deterministically and burned in **after** video generation, so no generative model ever touches the text.

Card copy, as built (template v4):

```
Raju Shetty                        ← Full Name
Shetty Motors                      ← Garage Name
Andheri, Mumbai | Mo. 9898989898   ← the address (free text, printed whole)
                                      and the mechanic's CONTACT number
```

**The number on the card is `mechanic_phone_number`, NOT the WhatsApp number.**
Two different numbers doing two different jobs, confirmed by the client
2026-09-08: `whatsapp_number` is the delivery key we POST back and is never
printed and never spoken; `mechanic_phone_number` is the contact number the card
prints. The renderer read the wrong one until 2026-09-15.

**Card geometry is fixed**, identical across all six plates, full video duration,
burned in after video generation. **v4 (2026-09-15) sits at `y 72.27% .. 87.00%`
of frame height** — 5.87% lower than v1's "just below the belt line", which was
chosen against a plate whose subject stood with folded arms. The avatar prompt
now parks the hands at belt height and keeps them there, measured at 60–70% of
frame height, which is exactly where v1's top edge sat: it cut across the
fingers. The band is now below them.

The rect is FIXED and the type scales to fit, not the other way round. A
content-driven height was built and rejected on 2026-09-15: it fixes the one
real cost of a fixed rect — two mechanics in a batch getting visibly different
type because one address wrapped — but a band that changes size between jobs is
the louder fault.

The fixed position is only safe if the person-replacement step preserves subject
scale and placement — see "Person replacement" below, and §11.

### Person replacement — scope of the edit

The model changes body build, age and skin tone, not just the head. An older, slighter man must not inherit a young muscular torso or mismatched hands.

This means **the plate is a pose and composition reference, not a frozen asset.** The chest panel and sleeve marks are re-rendered on every job, on a torso whose shape varies per person. They cannot be inherited from the plate and they cannot be composited, so the automated logo check is load-bearing rather than optional.

Prompt structure:

```
Change:    face, age, skin tone (face AND hands), build, facial hair
Preserve:  camera framing, crop, subject scale, head position,
           shoulder line, belt line, pose, hand position,
           uniform geometry, all Castrol marks, background
Constrain: no reframing, no zoom, no change to subject placement
```

Geometry preservation is not optional — a fixed card position depends on it. If the model shifts the subject vertically while changing build, the card drifts relative to the body and will either cover the hands or expose a mismatched thigh region.

**Glasses:** kept where present in the source photo. Rigid frames across a moving face are a known artifact source in lipsync generation, so include one glasses case in the stage C spike.

**Hand skin tone** is the most visible tell after the face. Explicit in the change list, and worth a dedicated check.

Useful side effect of the cap: it covers the top of the head, so tightly-cropped source photos with the skull cut off do not require the model to invent hair.

---

## 5. Pipeline stages

**Implemented in** [`stages/real.py`](../src/castrol_pipeline/stages/real.py) — one class per stage. Provider calls live in [`stages/vendors.py`](../src/castrol_pipeline/stages/vendors.py), local work in [`stages/media.py`](../src/castrol_pipeline/stages/media.py). See the stage table in [`README.md`](../README.md#the-pipeline).

| Stage | Input | Output | Notes |
|---|---|---|---|
| INTAKE | export API rows | validated job rows | reject non-compliant, do not repair |
| PREP | row | filled script, normalised address, plate id | numerals → words, abbreviations expanded |
| A: AUDIO | script + voice ref | wav | duration recorded |
| B: IMAGE | plate + owner photo | edited still | person replace only |
| C: VIDEO | B + A | mp4 | async submit, separate poller |
| D: COMPOSITE | C + card | final mp4 | deterministic, no model; also trims the silent tail |
| CHECKS | final mp4 | pass/fail + scores | logged regardless, non-blocking |
| PUBLISH | final mp4 | CDN URL | copies to `deliver/<uuid4>/` |
| DELIVER | CDN URL | POST `{phone, videoLink}` | gated by `DELIVERY_ENABLED` |

**There is no C2.** A conditional lipsync repair pass was in the original plan
and was dropped in migration `0003`: quality is solved in the main flow, and if
stage C output is unacceptable the fix is its inputs, not a patch stage. Do not
reintroduce it.

Per-stage queues with independent semaphores. Audio and image run ahead and buffer so video workers never wait upstream.

Per-stage idempotency by content hash of inputs: a stage whose input hash is unchanged is skipped on re-run. A failure at C must not re-run A and B.

The eight stages are one class each in
[`stages/real.py`](../src/castrol_pipeline/stages/real.py), driven twice a day
by `castrol cycle` — see [`TECH_DESIGN.md` §2](TECH_DESIGN.md).

---

## 6. Client interface contract

**Inbound** [`intake/export_client.py`](../src/castrol_pipeline/intake/export_client.py); **outbound** `DeliverStage` in [`stages/real.py`](../src/castrol_pipeline/stages/real.py), gated by `DELIVERY_ENABLED`.

### Inbound — daily export pull

```
GET https://capi.letschbang.com/api/submissions/export/vendor
    ?from=YYYY-MM-DD&to=YYYY-MM-DD
Header: apikey: <key>
→ CSV, 18 columns, UTF-8 with a BOM. Rate limit 100 / 900s.
```

**The response is CSV, not JSON.** Confirmed against a live pull on 2026-09-08.
The real header, in order:

```
id, whatsapp_number, user_name, workshop_name, address, gender,
mechanic_id_verified, mechanic_id, mechanic_phone_number, background,
outfit, image_url, image_mime_type, image_validation_status,
image_rekognition_status, status, createdAt, updatedAt
```

A sample row: `id` a uuid · `whatsapp_number` `918355837844` ·
`user_name` `Deeraj` · `workshop_name` `Sai Motors` · `address` `Worli` ·
`gender` `Male` · `mechanic_id_verified` `NOT VERIFIED` ·
`mechanic_id` `MECH|8932442` · `mechanic_phone_number` `8355837844` ·
`background` `SUV` · `outfit` `Castrol T-shirt` · `image_url` an Azure blob SAS
url · `image_validation_status` `APPROVED` ·
`image_rekognition_status` `FACE_DETECTED` · `status` `COMPLETED` ·
`createdAt` `2026-09-07T10:13:49.681Z`.

Four differences from the schema this section used to list, each of which broke
every row on its own until 2026-09-08:

- **There is a BOM.** Decoded as plain utf-8 the first header becomes `﻿id`,
  so `id` — the only unique identifier in the feed — silently reads as missing
  while the other 17 columns parse perfectly. `export_client.py` decodes
  `utf-8-sig`.
- **There are TWO phone columns.** `whatsapp_number` is the delivery key and the
  dedupe anchor; `mechanic_phone_number` is what the card prints. Neither is
  spoken. They are not interchangeable.
- **There is no `image_face_count`.** Rekognition arrives as a status string,
  which says a face was found but not how many — so the group-photo case is no
  longer detectable at intake and falls to the stage B checks.
- **`mechanic_id_verified` is a string**, not the boolean `has_mechanic_id` this
  plan assumed.

**Timestamps are ISO 8601** — `2026-09-07T10:13:49.681Z`, with and without
millis. The earlier `03-09-2026 14:35` / dd-MM-vs-MM-dd ambiguity belonged to
the pre-CSV schema and is gone. The format is still **pinned**
(`EXPORT_TIMESTAMP_FORMAT=iso8601`) and never inferred, and the raw string is
stored alongside the parsed value so a wrong format can be reparsed without
re-pulling.

**The address is free text, any shape** (client, 2026-09-09). It used to be
required to be exactly `Locality, City`, which rejected real people for writing
their own address normally — a one-word `Worli` was a `BAD_ADDRESS`. The card
prints the whole thing; the voice says only the last segment, because Indian
addresses run most-specific to least and reading it all aloud puts a hospital
landmark in a 30-second ad.

**Client-confirmed guarantees**, all encoded as CHECKS rather than assumptions,
so that if one stops holding we get a row with a stable reject code instead of a
broken video: `mechanic_phone_number` is never empty, `whatsapp_number` is
unique, `address` is never empty, `background` never holds a seventh value.

**Pull is not idempotent by itself.** Overlapping date windows will re-return
rows, and late submissions may land in a later window. Dedupe is ours, on the
submission hash **and** on the client's own row `id` — the second is what stops
an overlapping window turning a re-issued media url into a UNIQUE violation
that fails the whole batch.

### Photo access (Azure blob + SAS)

Photos arrive as Azure blob URLs with an embedded SAS token, expiry `se=2031-08-28`. Direct fetch, no additional credentials.

**Treat the URL as opaque bytes.** Encoding is inconsistent within a single URL — colons in `se=` are percent-encoded (`%3A`) while `sig=` carries a raw forward slash. Azure signs over exact bytes, so any re-encoding or normalisation breaks the signature and returns 403, which reads like a permissions failure rather than a parsing bug.

- Store the URL raw. Pass verbatim. Never rebuild from parsed components.
- Do not run it through `urllib.parse.quote`, a URL-normalising HTTP client, or any form-decoding path — base64 signatures can contain `+`, which becomes a space under form decoding.
- Worth an explicit code comment; this is the kind of thing a later refactor "fixes".

**Implementation notes**

- Validate `Content-Type` and magic bytes, not the status code.
- Download once at intake, straight to S3. Store `sha256` and our own key. Never fetch from the client's blob store at job time — our copy is the system of record.
- All SAS tokens are signed with one storage account key. If the client rotates it, every URL dies at once regardless of the 2031 expiry. The S3 copy is the mitigation.

**Photo quality floor:** minimum 100px on the short edge. This is a sanity check to reject empty files, thumbnails and broken uploads — not a quality gate. The client runs Rekognition upstream, which is the real filter.

Consequence accepted: output quality tracks input quality directly and there is no gate. Sampled real inputs range from a sharp 900x1600 portrait to a soft, tightly-cropped ~270x390 image with the top of the skull cut off. The second will produce a visibly softer result. This is expected, not a defect.

### Outbound — delivery webhook

```
POST https://capi.letschbang.com/api/webhook/video
Header: apikey, Content-Type: application/json
Body:  { "phone": "...", "videoLink": "https://..." }
```

**The join key is phone, not Mechanic ID.** Phone is the de facto primary key of the whole integration.

**There is no failure channel.** The webhook accepts a video link and nothing else. Decision taken: failures are recorded in Supabase after retries and surfaced in the admin panel only. No notification, no alert, nothing sent back to the client.

### Photo access (Google Drive) — superseded, removed

Photos arrived as Drive links under the earlier Google Sheets export. That is
gone: they are Azure blob SAS urls now, and the Drive-specific handling (file-ID
extraction, the `drive.usercontent.google.com` content endpoint, the sign-in
preview page that is not a permissions test, the uploader's Google account name
appended to filenames) has been deleted rather than left here to be mistaken
for current. Two things learned there survive as invariants because they are not
Drive-specific: a permissions failure can return **HTTP 200 with an HTML body**,
so validate magic bytes and never the status code (invariant 2); and the photo
is downloaded once at intake straight to S3, which is the system of record
(invariant 15).

---

## 7. Identity and keys

**Configured in** [`config.py`](../src/castrol_pipeline/config.py); documented in [`.env.example`](../.env.example). Account boundaries: [`CLAUDE.md`](../CLAUDE.md).

`mechanic_id` is not usable as a key. Values are inconsistent in format (`MECH|8932442`, bare integers of varying length), and the verification field can disagree with it. What the export sends is `mechanic_id_verified`, a string (`VERIFIED` / `NOT VERIFIED`); the boolean `has_mechanic_id` this plan originally assumed is derived from it at intake and is not supplied.

**The export's own `id` is the primary key on the client's side**, and intake requires it — a row without one is `MISSING_FIELD`. That is the identifier `mechanic_id` was hoped to be.

**Approach:**

- Internal `job_id` (UUID) is the primary key. Ours, always present, never null.
- `phone_e164` is the client join key — the delivery webhook accepts phone and nothing else.
- `mechanic_id` is stored as an opaque string, carried through, never trusted, never used for joins. Where missing or malformed, store the normalised value and flag it. The field is never null in the dashboard.
- **`media_key` = the blob path with the query string stripped** — e.g. `inbox_customer_to_agent/AuBQaWIlbgZF/wQiYtQWpeaYE.jpeg`. Those segments are unique per upload, which makes this the strongest available dedupe key. Preferred over any timestamp-based hash, since `created_at_ist` has no seconds.
- `submission_hash` = hash(`media_key` + `phone_e164`) for deduping repeated export pulls, **and** `client_submission_id` (the export's `id`) as a second anchor. Two keys rather than one because the media url can be re-issued between pulls: without the `id`, an overlapping window turns that into a UNIQUE violation that fails the whole batch.

---

## 8. Data model

**Implemented in** [`supabase/migrations/`](../supabase/migrations/) — forward-only
numbered SQL, applied in order, **0001–0014 applied**. The DDL is the source of
truth and carries the reasoning as column comments; what follows is the map.

| Table / view | Holds | Added by |
|---|---|---|
| `submissions` | one export row, validated — the two phone columns, the raw and spoken address, the verbatim `image_url_raw`, the reject code | `0001`, `0006` |
| `export_pulls` / `export_rows` | the raw export, byte-for-byte, so a parse can be redone without re-pulling | `0001`, `0005` |
| `jobs` | one video. `plate_id` is the only record of the artwork it was built from | `0001` |
| `stage_runs` | per-attempt state: `input_hash`, vendor, `vendor_task_id`, `model_id`, `params`, `cost_usd`, `billed_seconds`, `refunded` | `0001`, `0004`, `0013` |
| `assets` | every artefact by `kind`, with `s3_key`, `sha256`, dimensions, `cdn_url` | `0001`, `0004` |
| `checks` | machine-validation results, written regardless of outcome | `0001` |
| `deliveries` | the webhook POST and its response | `0001` |
| `plates` | the six combinations, append-only, with `uniform_ref_key` | `0001`, `0009` |
| `batches` | the morning summary row | `0001` |
| `vendor_limits` / `vendor_usage` | the daily caps and the independent count behind `reserve_vendor_call()` | `0002`, `0011`, `0012` |
| `job_events` | the durable timeline, credential-scrubbed | `0004` |
| `job_reports` | client review notes — the admin panel's only write | `0005` |
| `job_costs` (view) | per-video spend, **net of refunds**, with `refunded_usd` beside it | `0004`, `0013` |
| `job_usage` / `daily_usage` (views) | what the panel reads: DURATION only, no cost or vendor column to leak. `video_seconds` is the RENDER length, ceiled per row | `0007`, `0008`, `0010`, `0014` |

Three shapes worth stating because collapsing them looks like a cleanup:

- **`phone_e164` and `card_phone_e164` are different numbers.** Delivery key and
  printed contact number respectively (§6).
- **`plates` is append-only.** Re-registering retires the active row and inserts
  a new one; updating in place would silently rewrite what every shipped job
  claims it was made from.
- **Cost lives on `stage_runs`, per attempt**, never aggregated onto the job —
  and a terminal failure is marked `refunded`, because every failed vendor job
  refunds its credits.

RLS is deny-all with no policies on every table. The pipeline connects to
Postgres directly and uses no Supabase API key at all.

---

## 9. Input validation

**Implemented in** [`intake/validate.py`](../src/castrol_pipeline/intake/validate.py) and [`prep/normalise.py`](../src/castrol_pipeline/prep/normalise.py); reject codes in [`common/errors.py`](../src/castrol_pipeline/common/errors.py). Written against the real CSV export and confirmed against a live pull.

Applied at INTAKE. Non-compliant rows are rejected with a reason code and returned; they are not repaired in-pipeline.

| Field | Rule, as implemented |
|---|---|
| `id` | Required. The client's own row id, and the only unique identifier in the feed |
| `image_validation_status` | Must be `APPROVED` |
| `image_rekognition_status` | Must be `FACE_DETECTED`. **Not a count** — there is no `image_face_count` in the real export, so the group-photo case falls to the stage B checks. The reject code is still named `FACE_COUNT_NOT_1` |
| `image_mime_type` | Recognised image type; confirmed against **magic bytes**, never the declared value or the status code |
| Photo | Minimum 100px short edge. Sanity check only, not a quality gate |
| `whatsapp_number` | 10-digit Indian mobile → E.164. The delivery key |
| `mechanic_phone_number` | Same rule, separately. The card number |
| `user_name` | ≤ 30 chars, honorifics stripped (raised from 25 after a real row hit 23) |
| `workshop_name` | ≤ 30 chars |
| `address` | Non-empty, ≤ 90 chars. **Free text of any shape** — 90 is a sanity bound that catches a pasted paragraph, not a layout rule |
| `gender` | Male only this release |
| `background` | Mapped to a background id; client phrasings accepted as aliases |
| `outfit` | Mapped to a uniform id; same |
| Test rows | Name heuristics (`test`, `demo`, `dummy`, …) and repeated-digit phones |

Normalisation still runs on the address and is not about shape: it expands
abbreviations, says house numbers as numbers, drops the PIN code from speech,
and picks the **last segment** as the spoken locality. Transliteration variance
remains a TTS pronunciation risk regardless of format. `address_normalized`
holds the SPOKEN form, not a tidied postal address; `address_raw` is what the
card prints.

---

## 10. Decisions taken

| Item | Decision |
|---|---|
| Failure reporting | Supabase record after retries, visible in admin panel. No alerts, no client notification. |
| Duplicate phone | Not handled this release. |
| Submission ID | **Supplied** — the export's `id`, since the CSV schema (2026-09-08). Dedupe is on `media_key` + phone AND on that id. |
| Glasses | Keep if present in the source photo. |
| Photo source | Azure blob SAS URLs. Copy to S3 at intake; treat URL as opaque. |
| Card duration | Full video. |
| Card phone number | **`mechanic_phone_number`**, not the WhatsApp number — confirmed 2026-09-08, corrected in the renderer 2026-09-15. |
| Backgrounds | All 3 final. Vehicle-type → background mapping to be updated client-side in the mapping sheet. |
| Plates | All six exist. Artwork replaced 2026-09-11 (no cap, no sleeve logo, chest reads `Castrol`) and re-registered 2026-09-15. |
| Aspect ratio | Deferred. Re-framing handled with image generation later. |
| Output link lifetime | 6 months. |
| Photo resolution floor | 100px short edge, sanity check only. No quality gate. |
| Card position | Fixed, same across all plates, full duration. **Template v4: `y 72.27%..87.00%`** — moved below the hands 2026-09-15, not "just below the belt". |
| Uniform / background mapping | Mapped in `prep/plates.py` against the client's own combination map (2026-09-09). Uniform ids are `u1_tshirt` / `u2_uniform`. |
| Repair pass | Dropped (`0003`). Quality is solved in the main flow; if stage C output is unacceptable the fix is its inputs. |
| TTS provider | The voice provider, direct API — the one exception to "the two gateways only". Voice created by hand in the dashboard, referenced by id; no cloning call ships. |
| Video tier | `VIDEO_MODEL_ID` is the only resolution switch. Standard returns 720x1280, pro 1072x1920. Pro was declined 2026-09-10 and **reinstated 2026-09-12** when the client asked for 1080p; production still runs standard, which is a cost decision. |
| Run cadence | Twice daily, 00:00 and 12:00 IST, `castrol cycle` under a systemd timer on EC2. |
| Failed vendor calls | Refunded by the vendor, always. `job_costs` counts what was billed (`0013`); the daily caps still count failed attempts. |

## 11. Open issues

**Open:**

1. **Geometry / scale drift under a fixed-pixel card.** Spike 0.3, and the one
   question with no prior art — nothing in the existing BeHooked backend holds a
   subject at a fixed pixel scale across an image edit. The inputs it needed are
   now known (plates 1152x2048, card rect `y 72.27%..87.00%`, hands measured at
   60–70% of frame height across nine renders); what is missing is a per-job
   measurement. No check compares subject geometry against the plate, so drift
   would surface as a card over the hands in a render somebody happens to watch.
2. **The brand and geometry checks named as mitigations in §13 do not exist.**
   Three checks run — duration vs audio, vertical aspect, plausible bitrate. The
   chest-mark template match, the face-presence sample and the hand skin-tone
   check were never built. The chest mark is currently protected by the artwork
   and the image prompt, reviewed by eye.
3. **Throughput at volume.** Per-render latency is measured (~8–20 min); whether
   `MAX_CONCURRENCY_VIDEO=10` clears a big day is not. Observed volume is ~40
   videos, so this becomes real the first time a batch outlasts the 8-hour cycle
   deadline.

**Closed, with the answer:**

1. ~~**Script runtime.**~~ `kling-avatar-v2` has completed at 39s via the video provider and
   60s via fal. The ~80-word script at 30–40s is comfortably inside proven range
   and **needs no rewriting**; the original 18–25s assumption was conservative by
   about half. The real ceiling is *bytes, not seconds* — ship MP3, never WAV.
2. ~~**6-month links cannot be done with S3 presigned URLs.**~~ Resolved as
   recommended: **S3 private + CloudFront over an unguessable key**, with a
   180-day lifecycle rule deleting the object. The link dies because the object
   is removed, not because a signature lapsed. This is also why the worker holds
   static AWS keys rather than using its instance role — a URL signed with
   temporary credentials expires with the session token, well inside a render
   window.
3. ~~**`outfit` / `background` enums.**~~ Mapped 2026-09-09 from the client's own
   combination map; client phrasings accepted as aliases, and an unmapped value
   is a rejected row rather than a guess.
4. ~~**Timestamp format.**~~ ISO 8601, confirmed against a live pull. Still
   pinned in `EXPORT_TIMESTAMP_FORMAT`, still never inferred.
5. ~~**`has_mechanic_id` semantics.**~~ The export sends `mechanic_id_verified`
   as a string; the boolean is derived from it. Neither is joined on, and the
   panel does not present either as the mechanic's own ID.

---

## 12. Build plan

**All phases below have shipped** — the plan is kept as the record of what each
step had to prove. Current state is the Status block at the top of this file and
[`EC2_DEPLOYMENT.md`](EC2_DEPLOYMENT.md).

### Phase 0 — Spikes (before any pipeline code)

Throwaway scripts. The only goal is to kill assumptions that would force a rebuild later.

| # | Spike | Kills the assumption that |
|---|---|---|
| 0.1 | Stage C on one plate + one full-length audio | the script fits the avatar model's max input duration |
| 0.2 | Same, measuring wall-clock generation time | concurrency 10 clears a day's batch |
| 0.3 | Stage B person replacement on 3 real photos — sharp, soft, glasses | build/age/skin-tone transfer works with geometry locked |
| 0.4 | Inspect stage C output for the Castrol marks | brand marks survive two generative passes — and there are **two** marks now, not four: the 2026-09-11 artwork dropped the cap and the sleeve logo |
| 0.5 | Fetch 5 Azure SAS URLs verbatim through the real HTTP client | nothing in the stack re-encodes the signature |
| 0.6 | TTS one real address + the `8 seconds` numeral | normalisation rules are sufficient |

**Exit criteria:** one end-to-end video, hand-assembled, that a human would accept. Do not start Phase 1 without it.

---

### Phase 1 — Backend build

**1.1 Foundation**
- Repo, env config, secrets handling. Every vendor key and endpoint in env vars.
- Supabase schema per §8, migrations under version control.
- Structured logging with `job_id` on every line.
- Global daily call cap per vendor, hard stop. Build this first — it bounds every bug that follows.

**1.2 Intake**
- Export API client. Date-range pull, auth header, pagination if present.
- Row parser. Timestamp format pinned explicitly, never inferred.
- Validation per §9. Reject with reason code, do not repair.
- Dedupe on `media_key`.
- Test-row filter.
- Media fetcher: URL passed verbatim, magic-byte validation, S3 landing, `sha256` recorded.
- **Runs standalone.** Pull a real day, land real photos, produce real validated rows. Verify against the export before wiring anything downstream.

**1.3 Prep**
- Script template fill.
- Address and numeral normalisation, pronunciation override table.
- Plate selection from `background` + `outfit`.
- Pure functions, unit-tested. No I/O.

**1.4 Job orchestration**
- `stage_runs` state machine.
- Per-stage queues, independent semaphores.
- Postgres row claiming: `UPDATE ... FOR UPDATE SKIP LOCKED ... RETURNING`. Set `statement_cache_size=0` if connecting via the pooler port.
- Per-stage idempotency on `input_hash`; unchanged hash skips the stage.
- Per-stage retry with backoff, attempt cap.
- **Exercise with stub stages that sleep and return fixtures.** Prove the orchestrator before attaching paid vendors.

**1.5 Generation stages**
- A: TTS. Voice reference, duration recorded.
- B: image edit. Change/Preserve/Constrain prompt per §4 "Person replacement".
- C: video. Async submit, `vendor_task_id` stored, separate reconciling poller. Workers submit and release — never block a worker on a poll.
- ~~C2: conditional lipsync repair.~~ **Dropped in `0003`** and not to be reintroduced.
- Each stage behind one interface so a vendor swap is a config change.

**1.6 Composite and checks**
- Card renderer: deterministic, fixed geometry, text fitting rules.
- Burn-in after video generation.
- Machine checks. **Three were built** — audio/video duration delta, vertical aspect, plausible bitrate. Card text OCR match, chest-mark template match, face presence and geometry were specified here and never built; see §11.
- Results written to `checks` regardless of outcome. Logged, not blocking.

**1.7 Publish and deliver**
- S3 write, UUID key, Cloudflare CDN path.
- Lifecycle rule, 180-day expiry.
- Delivery webhook client: POST `{phone, videoLink}`, retry with backoff, response recorded in `deliveries`.

**1.8 Batch runner** — built as `castrol cycle`, twice daily rather than nightly, and it *waits* rather than draining: `drain` stops as soon as a sweep moves nothing, which for an async stage means "still rendering".
- Entry point: lock → pull → repair orphans → reopen suppressed deliveries → schedule → work/poll until quiet → stop at a deadline.
- Batch summary row: total, completed, failed, by-stage failure counts.
- Resume behaviour on restart mid-batch.

---

### Phase 2 — Admin panel

Built and live at <https://castrol-pipeline-admin-panel.vercel.app> — Next.js on Vercel, in-repo at [`panel/`](../panel/). Four pages: Jobs, Failures, Submissions, Usage, plus a per-job detail page. Auth is Supabase email + password with an `ADMIN_ALLOWED_EMAILS` allowlist; RLS is deny-all with no policies, so every query runs server-side with the secret key.

**It is a CLIENT-facing surface, not our operations console**, and that changed what it may show: no cost, no vendor, no model id, no stage, no retry count, no internal error code. The metric it reports is DURATION — the **render** length, CEILED to a whole second per video, never rounded and never shown with a decimal (migration `0014`, 2026-09-15). That is the unit the provider bills in: it charges per output second and rounds up, so a 24.2s render is billed as 25s, and a decimal would show a figure matching no invoice line. Totals are sums of already-ceiled per-render seconds, not the ceiling of a raw total — which was under-recovering by 4s across nine renders. That line is held structurally rather than by care — the pages read the `job_usage` / `daily_usage` views, which have no cost or vendor column in them. Per-job stage history and the failure breakdown "by stage" named above were therefore deliberately NOT built as such; Failures shows a reason sentence, not a stage. The one thing the panel writes is `job_reports`.

---

### Sequencing notes

1.1 → 1.2 → 1.3 can proceed without any vendor integration and should be verified against real client data first. 1.4 is provable with stubs. Only 1.5 spends money, and by then everything around it is known good.

Plate generation and card artwork were parallel tracks, not blockers for 1.1–1.4. Both are closed: all six plates exist with uniform references, and the card is at template v4.

---

## 13. Risks

**Mitigation here means something that exists.** Three rows in this table used to
name a machine check as the mitigation — chest/sleeve marks, subject scale, hand
skin tone — and those checks were never built (§11). They are marked as what they
actually are.

| Risk | Impact | Mitigation |
|---|---|---|
| SAS URL re-encoded anywhere in the stack | 403 that reads as a permissions failure | URL treated as opaque bytes, verbatim to the HTTP client; invariant 1, and a code comment in `intake/media.py` because this is what a refactor "fixes" |
| Client rotates storage account key | Every photo URL dies at once | S3 copy at intake is the system of record |
| Non-image body saved as `.jpg` | Silent corruption, wasted vendor spend | Validate magic bytes, not status or declared MIME; invariant 2 |
| Chest mark regenerated per job on a varying torso | Client-facing brand incident | **No automated check.** The artwork (single-word `Castrol`) and the image prompt's preserve clause, reviewed by eye. Nine of nine renders clean on 2026-09-14 |
| Subject scale drift breaks the fixed card position | Card covers the hands | Geometry lock in the prompt's Preserve/Constrain clauses, pinned by `tests/test_image_prompt.py`; the card moved below the hands in v4. **No per-job position check** |
| Hand skin tone inherited from the plate | Most visible tell after the face | Explicit in the prompt's change list. **No check** |
| Hands rendered badly by the avatar model | Visible defect in every frame | `AVATAR_PROMPT`: no gestures, hands low and apart, calm and slow motion — three paid revisions' worth of constraint, hashed as text so editing it regenerates |
| Export re-returns rows | Duplicate generation and spend | `submission_hash` **and** the client's row `id` |
| Retry loop | Runaway vendor spend | `reserve_vendor_call()` before every paid call, daily cost and call caps, `require_cost_estimate` on per-second vendors; caps count failed attempts even though the vendor refunds them |
| A cycle interrupted mid-intake | A mechanic silently never gets a video, with no error anywhere | `_repair_orphans` every cycle, logged at warning level; invariant 33 |
| Delivery recorded from an HTTP status | A video the mechanic never gets and nobody looks for | `webhook_accepted()` reads the body and fails closed; there is no failure channel back to the client |
| Systemic overnight failure | Whole batch silently produces nothing | Batch summary row, `castrol report`, and the panel. **No alerting, by decision** |
| Address mispronounced | Video is useless to the mechanic | Normalisation + spoken-locality rules, unit-tested |
| A merge reaching the worker unreviewed | A render that looks wrong, shipped | The worker tracks `:latest` with `--pull always`, so this is real: prove prompt, model-id and card changes through `spikes/prototype.py` or pin a `main-<sha>` first |
