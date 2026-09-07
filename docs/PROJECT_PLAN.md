# Castrol MAGNATEC — Mechanic Promo Video Pipeline

**Status:** Plan complete — Phase 0 spikes next
**Last updated:** 07 Sep 2026
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

```
   Client export API  (daily pull, from/to date range)
            │
            ▼
   INTAKE ──── validate, normalise, dedupe, filter test rows
            │  reject file → back to client
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
            │
            ▼
   C2: REPAIR  (conditional lipsync pass)
            │
            ▼
   D: COMPOSITE  burn personalisation card
            │
            ▼
   CHECKS   machine validation, log always
            │
            ▼
   S3 + CDN ──▶ POST client delivery webhook {phone, videoLink}
```

Infrastructure unchanged: AWS S3, Supabase Postgres, AWS EC2, GitHub, Cloudflare/Vercel for the panel.

---

## 4. Assets

### Plate matrix — 6 combinations

| Combo | Uniform | Background |
|---|---|---|
| 1 | Polo T-shirt | BG1 — indoor garage, white SUV, red tool cart |
| 2 | Polo T-shirt | BG2 — indoor garage, dark sedan |
| 3 | Polo T-shirt | BG3 — weathered garage, hatchback, open hood |
| 4 | Half sleeve shirt | BG1 |
| 5 | Half sleeve shirt | BG2 |
| 6 | Half sleeve shirt | BG3 |

Supplied so far: BG1/2/3 rendered with Uniform 1, plus a two-up uniform reference. **Three plates (Uniform 2 × BG1/2/3) do not exist yet.**

Plates are generated once, human-approved once, and frozen. Uniform and car are never generated at runtime.

### Brand marks per frame

Four instances, all of which must survive both generative passes:

1. Cap — Castrol MAGNATEC
2. Chest panel — Castrol MAGNATEC
3. Sleeve — Castrol
4. Overhead banner — Castrol Service / Castrol MAGNATEC

The cap is a useful side effect: it standardises the head silhouette, which makes the person-replacement step more reliable across varied source photos and removes most hair/headwear variance.

### Personalisation card

Per the client draft, the mechanic's details appear as a **lower-third card composited over the video** — not as text rendered into the garage scene. The garage banner stays fixed Castrol branding.

This removes generative text rendering from the pipeline entirely. Card is drawn deterministically and burned in **after** video generation, so no generative model ever touches the text.

Card copy (draft, client to finalise):

```
Raju Shetty          ← Full Name
Shetty Motors        ← Garage Name
Andheri, Mumbai      ← Address (Location, City)
Mo. 9898989898       ← WhatsApp Number
```

**Card geometry is fixed.** Position sits just below the belt line, identical across all six plates, since every plate shares the same uniform framing and subject placement. Full video duration. Burned in after video generation.

The fixed position is only safe if the person-replacement step preserves subject scale and placement — see §4.4.

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

| Stage | Input | Output | Notes |
|---|---|---|---|
| INTAKE | export API rows | validated job rows | reject non-compliant, do not repair |
| PREP | row | filled script, normalised address, plate id | numerals → words, abbreviations expanded |
| A: AUDIO | script + voice ref | wav | duration recorded |
| B: IMAGE | plate + owner photo | edited still | person replace only |
| C: VIDEO | B + A | mp4 | async submit, separate poller |
| C2: REPAIR | C + A | mp4 | conditional |
| D: COMPOSITE | C/C2 + card | final mp4 | deterministic, no model |
| CHECKS | final mp4 | pass/fail + scores | logged regardless |
| PUBLISH | final mp4 | CDN URL | then POST webhook |

Per-stage queues with independent semaphores. Audio and image run ahead and buffer so video workers never wait upstream.

Per-stage idempotency by content hash of inputs: a stage whose input hash is unchanged is skipped on re-run. A failure at C must not re-run A and B.

---

## 6. Client interface contract

### Inbound — daily export pull

```
GET https://capi.letschbang.com/api/submissions/export/vendor
    ?from=YYYY-MM-DD&to=YYYY-MM-DD
Header: (auth pending from client)
```

Current schema (supersedes the earlier Google Sheets export):

```
user_name                  Deeraj
workshop_name              Sai Motors
address                    Dombivili, Thane          ← Locality, City
gender                     Male
has_mechanic_id            FALSE
mechanic_id                MECH|8932442
mechanic_phone_number      8355837844
background                 SUV                       ← vehicle-type named
outfit                     Castrol T-shirt
image_url                  https://interaktprodmediastorage.blob.core.windows.net/...
image_mime_type            image/jpeg
image_validation_status    APPROVED
image_rekognition_status   FACE_DETECTED
image_face_count           1
status                     COMPLETED
created_at_ist             03-09-2026 14:35
updated_at_ist             03-09-2026 15:57
```

Improvements over the previous schema: address already arrives as `Locality, City`, upstream Rekognition results are exposed, and photos are Azure blobs rather than Drive links.

**Timestamp format is ambiguous.** `03-09-2026 14:35` — dd-MM vs MM-dd is not determinable from the value, there are no seconds, and no timezone marker beyond the column name. Since the export API takes date ranges, a misparse silently shifts the entire pull window. Pin the format explicitly; do not let a parser infer it.

**Pull is not idempotent by itself.** Overlapping date windows will re-return rows, and late submissions may land in a later window. Dedupe on our side.

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

### Photo access (Google Drive)

Photo URLs in the export are Drive links in `open?id=` form. All variants (`/file/d/ID/view`, `/open?id=ID`, `/uc?id=ID`, `/uc?export=download&id=ID`) carry the same file ID; one regex extracts it.

**Tested 07 Sep 2026: files are publicly fetchable, no authentication required.**

```
curl -sL "https://drive.usercontent.google.com/download?id=FILE_ID&export=download"
→ 200  image/jpeg  84045 bytes  (736x1104 progressive JPEG)
```

Note that the `/file/d/ID/view` preview page renders a sign-in prompt even for link-shared files. **The preview page is not a permissions test.** Only the `drive.usercontent.google.com/download` content endpoint gives a true answer.

**No client action required.** Extract the file ID and fetch from the content endpoint.

Durable improvement, not a blocker: ask the client to share the folder with a service account and use Drive API `files.get(fileId, alt=media)`. Current access depends on the folder staying link-shared; if anyone tightens it, every row breaks at once with no warning. Also worth flagging to the client that a publicly-fetchable folder of face photos, joinable to a sheet of phone numbers, is a weak DPDP position — their call.

**Implementation notes**

- A permissions failure returns **HTTP 200 with an HTML body**. Validate `Content-Type` and magic bytes, never the status code. Without this, a revoked share writes login pages into S3 as `.jpg`.
- Download once at intake, straight to S3. Store `sha256` and our own key. Never fetch from Drive at job time — our copy is the system of record.
- Rows may carry multiple comma-separated URLs. Take the first, flag the row.
- Throttle; Drive rate-limits tight loops.
- Files over ~100MB get a confirmation interstitial instead of bytes. Photos won't hit this, but handle it.
- Forms appends the **uploader's Google account name** to each filename (`<hash> - <Name>.jpg`). This is the Google identity of whoever uploaded, not necessarily the mechanic. Do not parse it as the name. Usable as a duplicate-submission signal.

**Photo quality floor:** minimum 100px on the short edge. This is a sanity check to reject empty files, thumbnails and broken uploads — not a quality gate. The client runs face detection on their side, which is the real filter.

Consequence accepted: output quality tracks input quality directly and there is no gate. Sampled real inputs range from a sharp 900x1600 portrait to a soft, tightly-cropped ~270x390 image with the top of the skull cut off. The second will produce a visibly softer result. This is expected, not a defect.

---

## 7. Identity and keys

`mechanic_id` is not usable as a key. Values are inconsistent in format (`MECH|8932442`, bare integers of varying length), and `has_mechanic_id` can read FALSE while `mechanic_id` is populated — the two fields do not agree.

**Approach:**

- Internal `job_id` (UUID) is the primary key. Ours, always present, never null.
- `phone_e164` is the client join key — the delivery webhook accepts phone and nothing else.
- `mechanic_id` is stored as an opaque string, carried through, never trusted, never used for joins. Where missing or malformed, store the normalised value and flag it. The field is never null in the dashboard.
- **`media_key` = the blob path with the query string stripped** — e.g. `inbox_customer_to_agent/AuBQaWIlbgZF/wQiYtQWpeaYE.jpeg`. Those segments are unique per upload, which makes this the strongest available dedupe key. Preferred over any timestamp-based hash, since `created_at_ist` has no seconds.
- `submission_hash` = hash(`media_key` + `phone_e164`) for deduping repeated export pulls.

---

## 8. Data model

```
submissions   id, submission_hash, media_key, pulled_at, raw jsonb,
              phone_e164, user_name, workshop_name, address_raw,
              address_normalized, gender, mechanic_id, has_mechanic_id,
              background_choice, outfit_choice,
              image_url_raw, image_mime_type,
              image_validation_status, image_rekognition_status, image_face_count,
              client_status, created_at_ist, updated_at_ist,
              is_test, validation_status, reject_reason

jobs          id, submission_id, status, current_stage, plate_id,
              script_version, voice_id, created_at, completed_at

stage_runs    id, job_id, stage, input_hash, status, attempts,
              vendor, vendor_task_id, model_id, params jsonb,
              output_key, started_at, finished_at, error_code, error_message

assets        id, job_id, kind, s3_key, sha256, bytes,
              duration_ms, width, height, created_at

checks        id, job_id, check_name, passed, score, details jsonb

deliveries    id, job_id, phone_e164, cdn_url, posted_at,
              response_code, response_body, attempts

plates        id, uniform_id, background_id, s3_key,
              approved_by, approved_at, active
```

---

## 9. Input validation

Applied at INTAKE. Non-compliant rows are rejected with a reason code and returned; they are not repaired in-pipeline.

| Field | Rule |
|---|---|
| `image_validation_status` | Must be `APPROVED` |
| `image_face_count` | Must equal 1. A group photo can pass "face detected" and still break stage B. |
| `image_mime_type` | Recognised image type; confirm against magic bytes, not the declared value |
| Photo | Minimum 100px short edge. Sanity check only. |
| `mechanic_phone_number` | 10-digit Indian mobile, normalise to E.164 |
| `user_name` | ≤25 chars, no honorifics |
| `workshop_name` | ≤ card width limit (TBC from final card artwork) |
| `address` | `Locality, City`, ≤ length limit (TBC), abbreviations expanded, no PIN, no shop/plot number, no numerals |
| `gender` | Male only this release |
| `background` | Enum, maps to background plate |
| `outfit` | Enum, maps to uniform |
| Test rows | Filtered by name/workshop heuristics + explicit test list |

Address quality has improved markedly in the current schema (`Dombivili, Thane`), but normalisation still runs — earlier samples carried shop numbers, house numbers and inconsistent spellings of the same locality, and transliteration variance remains a TTS pronunciation risk regardless of format.

---

## 10. Decisions taken

| Item | Decision |
|---|---|
| Failure reporting | Supabase record after retries, visible in admin panel. No alerts, no client notification. |
| Duplicate phone | Not handled this release. |
| Submission ID | None supplied. Dedupe on `media_key` (blob path). |
| Glasses | Keep if present in the source photo. |
| Photo source | Azure blob SAS URLs. Copy to S3 at intake; treat URL as opaque. |
| Card duration | Full video. |
| Card phone number | WhatsApp number from the export. |
| Backgrounds | All 3 final. Vehicle-type → background mapping to be updated client-side in the mapping sheet. |
| Plates | We generate the 3 missing Uniform-2 plates. |
| Aspect ratio | Deferred. Re-framing handled with image generation later. |
| Output link lifetime | 6 months. |
| Photo resolution floor | 100px short edge, sanity check only. No quality gate. |
| Card position | Fixed pixel position just below the belt, same across all plates. Full duration. |
| Uniform / background mapping | Handled client-side later; mapping sheet to be updated. |

## 11. Open issues

1. **Script runtime.** The finalised script is ~80 words, landing around 30–40s spoken, well above the 18–25s originally assumed. Confirm against the avatar model's maximum input duration before anything else. If it caps below that, the script changes, not the pipeline.
2. **6-month links cannot be done with S3 presigned URLs.** SigV4 caps presigned expiry at 7 days (604800s), a hard AWS limit. Worse, a URL signed with temporary EC2 instance-role credentials dies when the session token expires — typically within the hour — regardless of the expiry set.

   Workable approaches:
   - **S3 private + Cloudflare CDN + unguessable object key** (UUID path), with an S3 lifecycle rule deleting at 180 days. Stable link, scheduled disappearance, uses infrastructure already in the stack. Recommended.
   - **CloudFront signed URLs** if cryptographic expiry is required rather than unguessability. Arbitrary expiry, but adds a distribution and key pair to manage.
3. **`outfit` / `background` enums** — exact allowed value strings. `Castrol T-shirt` and `SUV` confirmed; remaining values unknown.
4. **Timestamp format** — confirm dd-MM-yyyy with the client before wiring the date-range pull.
5. **`has_mechanic_id` semantics** — reads FALSE while `mechanic_id` is populated. Confirm before the panel displays it as the mechanic's own ID.

---

## 12. Build plan

### Phase 0 — Spikes (before any pipeline code)

Throwaway scripts. The only goal is to kill assumptions that would force a rebuild later.

| # | Spike | Kills the assumption that |
|---|---|---|
| 0.1 | Stage C on one plate + one full-length audio | the script fits the avatar model's max input duration |
| 0.2 | Same, measuring wall-clock generation time | concurrency 10 clears a day's batch |
| 0.3 | Stage B person replacement on 3 real photos — sharp, soft, glasses | build/age/skin-tone transfer works with geometry locked |
| 0.4 | Inspect stage C output for all 4 Castrol marks | brand marks survive two generative passes |
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
- B: image edit. Change/Preserve/Constrain prompt per §4.4.
- C: video. Async submit, `vendor_task_id` stored, separate reconciling poller. Workers submit and release — never block a worker on a poll.
- C2: conditional lipsync repair.
- Each stage behind one interface so a vendor swap is a config change.

**1.6 Composite and checks**
- Card renderer: deterministic, fixed geometry, text fitting rules.
- Burn-in after video generation.
- Machine checks: card text OCR match, chest mark template match, face present across sampled frames, audio/video duration delta, file integrity, resolution, duration, size.
- Results written to `checks` regardless of outcome. Logged, not blocking.

**1.7 Publish and deliver**
- S3 write, UUID key, Cloudflare CDN path.
- Lifecycle rule, 180-day expiry.
- Delivery webhook client: POST `{phone, videoLink}`, retry with backoff, response recorded in `deliveries`.

**1.8 Batch runner**
- Nightly entry point: pull → enqueue → drain → report.
- Batch summary row: total, completed, failed, by-stage failure counts.
- Resume behaviour on restart mid-batch.

---

### Phase 2 — Admin panel

Supabase queries over the schema above. Submissions table, per-job stage history, video preview, batch summaries, failure breakdown by stage and error code. Auth, RLS deny-all by default, service key server-side only.

---

### Sequencing notes

1.1 → 1.2 → 1.3 can proceed without any vendor integration and should be verified against real client data first. 1.4 is provable with stubs. Only 1.5 spends money, and by then everything around it is known good.

Plate generation (3 missing Uniform-2 plates) and card artwork are parallel tracks, not blockers for 1.1–1.4.

---

## 13. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| SAS URL re-encoded anywhere in the stack | 403 that reads as a permissions failure | URL treated as opaque; verified in spike 0.5 |
| Client rotates storage account key | Every photo URL dies at once | S3 copy at intake is the system of record |
| Non-image body saved as `.jpg` | Silent corruption, wasted vendor spend | Validate magic bytes, not status or declared MIME |
| Timestamp misparsed dd-MM vs MM-dd | Pull window silently wrong | Format pinned with client, not inferred |
| Chest/sleeve marks regenerated per job on varying torso | Client-facing brand incident | Machine check on every video, logged |
| Subject scale drift breaks fixed card position | Card covers hands or exposes mismatch | Geometry lock in preserve list; per-job position check |
| Hand skin tone inherited from plate | Most visible tell after the face | Explicit in change list; dedicated check |
| Script longer than model's max audio duration | Rework of script or vendor | Verify in the stage C spike |
| Presigned URL expiry misunderstood | Links dead within the hour | CDN + unguessable key + lifecycle rule |
| Address mispronounced | Video is useless to the mechanic | Normalisation + pronunciation overrides |
| Export re-returns rows | Duplicate generation and spend | `submission_hash` dedupe |
| Systemic overnight failure | Whole batch silently produces nothing | Batch completed/failed counts on panel |
| Retry loop | Runaway vendor spend | Global daily call cap per vendor |
