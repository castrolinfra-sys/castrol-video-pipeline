# Talking-Head Pipeline — Integration Reference (apimart + kie)

**Compiled:** 2026-09-07
**Sources:** `behooked_studio_backend` working tree + prod Supabase `app_db` (`qpmqcscuetaxjylhedrq`), read-only.
**No secret values in this file.** Credentials are referenced by env var name only.

### Evidence legend

| Tag | Meaning |
|---|---|
| **[CODE]** | Read out of the live backend source. Authoritative for payload shape. |
| **[PROD]** | Measured from prod job rows. Authoritative for "does this actually work". |
| **[GAP]** | We do not have this. Stated as a gap, not guessed. |

Where this document says "we", "our", "prod" — it means the existing BeHooked Studio backend and its live database, which is the body of evidence being mined. It does **not** mean the talking-head pipeline being built. Those are separate systems; this is a transfer of hard-won knowledge from one to the other.

### Contents

| § | | |
|---|---|---|
| **0** | **Read this first** | pipeline table, spike 0.1 answer, design-changing findings, end-to-end call sequence, cost, known unknowns |
| 1 | Gateway basics | kie + apimart envelopes, status enums, idempotency, webhook auth |
| 2 | TTS | what's proven, cloning flow, length limits, duration, digit handling, billing |
| 3 | Video / avatar lipsync | duration evidence, payload, latency, result-URL lifetime |
| 4 | Image edit / person replacement | model, URL passing, prod failure profile, prompt structure, drift |
| 5 | Lipsync repair | what doesn't exist, and the nearest usable tool |
| 6 | Cost per call | rate tables by model and lane, worked example |
| 8 | Gotchas | 19 failure modes already paid for |
| — | Open questions | 8 items, ordered by what they block |

*(Section numbering follows the original request, which had no item 7.)*

---

## 0. Read this first

### 0.1 The pipeline, resolved

| # | Step | Model | Lane | Cost | Latency | Confidence |
|---|---|---|---|---:|---|---|
| 1 | Person/plate edit | `gpt-image-2-max` (apimart endpoint `gpt-image-2`) | **apimart** | $0.012 @2K | ~83 s avg, 644 s worst | **High** — 787 completed in prod, still in daily use |
| 2 | Voice | **unresolved** — see §0.3 | — | $0.06–$0.10 / 1k chars | seconds (sync) | **Low** — the constraint has no solution as stated |
| 3 | Avatar lipsync | `kling-avatar-v2` (`kling/ai-avatar-standard\|pro`) | **kie** | $0.04/s std · $0.08/s pro | **8–20 min** at 30–40 s | **High** — proven at 39 s on kie, 60 s on fal |
| 4 | Repair (optional) | `sync-lipsync-v2` | **fal only** | $0.04/s std · $0.06/s pro | ~12 min | **Medium** — 40 completed, but no trigger signal exists |

Steps 1 and 3 land cleanly on your preferred gateways. Step 2 does not. Step 4 has no gateway lane at all.

### 0.2 Spike 0.1 answered: **do not rewrite the script**

`kling-avatar-v2` has completed in prod at **39 s / 30 s / 26 s / 25 s / 24 s / 21 s / 20 s / 19 s / 16 s via kie**, and up to **60 s via fal**. The v1 sibling has gone to **113 s**. Your ~80-word script at 30–40 s is comfortably inside proven territory — the original 18–25 s assumption was too conservative by roughly half.

**The real ceiling is bytes, not seconds.** Every `"Audio size is too large"` failure in the entire history was a `.wav`. A 37 s WAV failed while a 53 s WAV succeeded; a 116 s WAV failed while the *same user's* 113 s MP3 succeeded four days earlier. The threshold is a byte count that moves with sample rate, bit depth and channel count.

> **Decision: encode the TTS output to MP3 before the avatar step.** This removes the entire failure class. Note that our own Cartesia path *emits* `pcm_f32le` WAV at ~176 KB/s (~7 MB for 40 s, against ~640 KB as MP3) — so if you use Cartesia, the transcode is mandatory, not optional. Full evidence in §3.2.

**Budget 8–20 minutes** of wall clock for a 30–40 s render. The 39 s job took ~1191 s. Do not build a UI or an SLA that promises two minutes.

### 0.3 Three findings that should change the design

**(a) Neither gateway supports an idempotency key on submit.** Not kie, not apimart — no such field exists on either. A network-level retry of a submit creates a second provider job and a second charge. Retry safety must be built application-side *before* the HTTP call. Our implementation (`core_idempotency_keys`, `UNIQUE (user_id, idempotency_key)`, three-branch record-or-replay, plus an independent duplicate-completion guard at the webhook) is described in §1.3 and is the shape worth copying.

**(b) TTS never returns duration.** Nothing in any TTS response — ElevenLabs, Cartesia, MiniMax — carries a duration. It must be probed with ffmpeg. Because the avatar model bills **per output second**, that probe sits directly in your charge path, which makes it a security and billing surface, not a convenience: a client-supplied duration is an untrusted billing input. Our resolution ladder is *probe wins -> plausible client hint -> fail closed to the cap*, never to zero (a broken probe returning 0 becomes free generations). And note the trap: `kling-avatar-v2`'s `fallback_duration` is **5 seconds** — miss the probe and you bill 5 s for a 35 s video. Full detail in §2.5.

**(c) The TTS requirement as stated has no solution.** "apimart or kie" ∩ "clone from a client reference" ∩ "Hindi/Hinglish male" is currently an empty set:

- The only ElevenLabs TTS configured on kie (`elevenlabs-tts-multilingual-v2`) has **zero successful generations, ever**, and exposes **no cloning parameter at all** — only 20 English presets.
- The only thing verified on Hindi *and* Gujarati *with a clone* is **Cartesia**, which runs on a direct API with **no kie or apimart lane**.
- No male-Hindi preset exists in any lane we have configured.

This is question 1 of the open questions at the end, and it blocks step 2. Everything else in this document is buildable today.

### 0.4 End-to-end call sequence

The four submits in order, as actual wire payloads. Detail and caveats for each are in the numbered sections.

```
STEP 1 — person/plate edit                                            [§4]
POST https://api.apimart.ai/v1/images/generations
Authorization: Bearer $APIMART_API_KEY
{
  "model": "gpt-image-2",
  "prompt": "<'reproduce EXACTLY as-is / change exactly one thing' framing — §4.4>",
  "image_urls": ["https://<public-or-presigned, /source/ path, 300-6000px both axes>"],
  "size": "9:16", "resolution": "2K", "n": 1, "official_fallback": false
}
-> { "code": 200, "data": [ { "status": "submitted", "task_id": "task_..." } ] }
   poll GET /v1/tasks/{task_id} until status ∈ {completed, failed, cancelled}
   result image URL -> copy to your own storage immediately

STEP 2 — voice                                                        [§2]
(a) clone once, persist the id — NOT per call:
POST https://api.cartesia.ai/voices/clone      multipart: clip, name, language, enhance
-> { "id": "<voice_id>" }
(b) synthesize, synchronous, returns raw bytes:
POST https://api.cartesia.ai/tts/bytes
{ "model_id": "sonic-3.5", "transcript": "<script>",
  "voice": { "mode": "id", "id": "<voice_id>" },
  "output_format": { "container": "wav", "encoding": "pcm_f32le", "sample_rate": 44100 } }
-> raw audio bytes. No duration in the response.

STEP 2.5 — MANDATORY GLUE, not a provider call
  transcode WAV -> MP3                 (§0.2 — this is what prevents "Audio size is too large")
  ffprobe the MP3 for duration          (§2.5 — nothing upstream gives it to you)
  upload to storage the provider can fetch; presigned URLs live 1 h (§3.6)

STEP 3 — avatar lipsync                                               [§3]
POST https://api.kie.ai/api/v1/jobs/createTask
Authorization: Bearer $KIE_API_KEY
{
  "model": "kling/ai-avatar-standard",          // or kling/ai-avatar-pro
  "callBackUrl": "https://.../<owner_id>/<hmac>",
  "input": { "image_url": "<step 1 output>", "audio_url": "<step 2.5 MP3>", "prompt": "." }
}
-> { "code": 200, "data": { "taskId": "..." } }
   callback or poll GET /api/v1/jobs/recordInfo?taskId=...
   data.state: waiting -> queuing -> generating -> success | fail
   data.resultJson is a JSON *STRING* -> parse -> resultUrls[0]
   copy to your own storage inside the handler

STEP 4 — repair, only if you decide a trigger                         [§5]
fal-ai/sync-lipsync/v2  { video_url, audio_url, sync_mode: "cut_off" }
No kie or apimart lane exists for this. No quality signal exists to trigger it.
```

### 0.5 What this costs, and what that means for the cap

One 35 s talking head:

| Step | Choice | Provider cost | Share |
|---|---|---:|---:|
| Image edit | `gpt-image-2-max` @ 2K, apimart | $0.012 | 0.8 % |
| TTS | Cartesia clone, 550 chars -> 1k billed | $0.100 | 6.6 % |
| Avatar | `kling-avatar-v2` **standard**, kie, 35 s | **$1.400** | **92.6 %** |
| **Total** | | **$1.512** | |
| Same with `pro` | | $2.912 | avatar = 96 % |
| Same with `pro` + unconditional repair | | $4.312 | video steps = 97 % |

**The avatar step is 93–96 % of the spend and it is priced per second, not per call.** A call-count cap therefore bounds volume but bounds *spend* only within a factor that varies ~3× on script length and ~2× on variant choice. Cap on **seconds** or on **dollars**, and put the ceiling on the avatar step specifically — capping the image and TTS steps is rounding error.

**[GAP]** `vendor_limits`, `daily_credit_cap` and `NUMERAL_WORDS` do not appear anywhere in these six repos. They belong to the pipeline being built, not to this codebase, so §6 gives raw rates rather than a mapping into a schema I have not seen. See question 7.

### 0.6 Where the evidence runs out

Three things this document cannot answer, stated plainly so nobody builds on a guess:

| Unknown | Why it matters | Section |
|---|---|---|
| **Geometry / scale drift under a fixed-pixel composite** | Your card sits at a fixed pixel rect. Nothing in this codebase preserves a pixel-locked region across an image edit — every image path here is "generate a good-looking frame", never "hold this region invariant". Zero prior art. | §4.5, Q4 |
| **Any lipsync quality signal** | There is no score, no confidence, no threshold anywhere in the backend. The trigger your repair step needs is missing from ours too. | §5, Q6 |
| **Hindi output *quality*** | "Completed" in the DB means audio returned and was charged. Nobody recorded whether the pronunciation was acceptable or how Latin digits were read aloud. The database cannot tell you this. | §2.2, §2.6 |

### 0.7 The most transferable lesson

§8.11 is the one to read even if you skip everything else. An ElevenLabs lane on kie returned **422 on every single request for months** — because we copied parameter names from a *sibling endpoint on the same gateway* that happened to accept them. Our fallback chain classified the 422 as retryable, silently fell through to a lane costing **3× more**, and left **no trace in the database**. The config advertised the dead lane as priority 1 the entire time.

Two structural rules come out of it, and both apply directly to a new multi-gateway pipeline:

1. **Validate against the exact endpoint's schema.** Sibling endpoints on the same gateway differ in ways their docs do not make obvious.
2. **A fallback chain that swallows the reason is a cost leak you cannot see.** Record which provider was tried and why it lost, or this failure is undetectable until someone audits the bill.

---

## 1. Gateway basics

### 1.1 kie.ai

| Item | Value |
|---|---|
| Base | `https://api.kie.ai` |
| Submit | `POST /api/v1/jobs/createTask` |
| Alt submit (some models) | `POST https://api.kie.ai{api_path}` — flat body, no `input` wrapper (e.g. veo3 `/api/v1/veo/generate`) |
| Auth | `Authorization: Bearer $KIE_API_KEY` + `Content-Type: application/json` |
| Poll | `GET /api/v1/jobs/recordInfo?taskId=<id>` |
| Delivery | Webhook (`callBackUrl` in the submit body) **and** polling. Both work. |
| Idempotency key on submit | **None.** No such field exists. |

**[CODE]** `utils/provider_clients.py:KieClient`, `models/app_models/providers/kie_client.py`, `test_kie_upscale.py`

Submit envelope:

```json
{
  "model": "kling/ai-avatar-pro",
  "callBackUrl": "https://api.example.com/webhook/lipsync_generation/<user_id>/<hmac>",
  "input": { "image_url": "...", "audio_url": "...", "prompt": "." }
}
```

Submit response:

```json
{ "code": 200, "msg": "success", "data": { "taskId": "..." } }
```

Poll / callback body:

```json
{ "data": {
    "taskId": "...",
    "state": "waiting | queuing | generating | success | fail",
    "resultJson": "{\"resultUrls\":[\"https://...\"]}",
    "failCode": "...", "failMsg": "...", "errorReason": "...", "msg": "..."
} }
```

Hard-won parsing rules **[CODE]**:

- `state` progression: `waiting -> queuing -> generating -> success | fail`. Our failure set is `{"fail","FAILED","failed","error","ERROR"}` — kie has used more than one casing.
- **`resultJson` is a JSON *string*, not an object.** Parse it, then read `resultUrls[]`.
- **HTTP 200 with `code != 200` is an error.** Always check the body code, not just the status line.
- Error text priority we settled on: `failMsg` -> `errorReason` -> `msg`, prefixed with `failCode` when present.

Type coercions kie requires that cost us real submits **[CODE]** (`KieClient.submit`):

- `num_images` must be a **string** (`"1"`), not an int.
- `image_input` must **always be present** — an empty array `[]` for text-to-image, not omitted.
- `image_urls` / `video_urls` / `input_urls` must be **arrays** even for one item.
- `duration` is an **int** for most models but a **string** for wan-2.5. We carry a per-provider `kie_string_duration` flag. Expect more of this class.

### 1.2 apimart.ai

| Item | Value |
|---|---|
| Base | `https://api.apimart.ai/v1` |
| Submit (image) | `POST /v1/images/generations` |
| Submit (video) | `POST /v1/videos/generations` |
| Auth | `Authorization: Bearer $APIMART_API_KEY` + `Content-Type: application/json` |
| Poll | `GET /v1/tasks/{task_id}` |
| Delivery | **Polling only. No webhooks.** |
| Idempotency key on submit | **None.** |

**[CODE]** `utils/provider_clients.py:ApimartClient`, `models/app_models/providers/apimart_client.py`

Submit envelope — **flat, no `input` wrapper**:

```json
{ "model": "gpt-image-2", "prompt": "...", "image_urls": ["https://..."], "size": "1:1", "n": 1 }
```

Submit response — **`data` is an array**:

```json
{ "code": 200, "data": [ { "status": "submitted", "task_id": "task_..." } ] }
```

Poll response:

```json
{ "code": 200, "data": { "status": "pending|processing|completed|failed|cancelled",
                         "result": { ... }, "error": { "code": ..., "message": "..." } } }
```

- Terminal states: `completed`, `failed`, `cancelled`. Everything else is non-terminal.
- **Video result is double-nested:** `result.videos[0].url` is a **list** — you want `result.videos[0].url[0]`.
- Error body is inconsistent: dict `{"error":{"message":...}}`, a bare string, or a *stringified* JSON object. Our parser handles all three (`parse_apimart_error`).

Polling parameters we converged on after production incidents **[CODE]**:

| Knob | Value | Why |
|---|---|---|
| Submit timeout | **60 s** | 15 s produced orphaned jobs: apimart accepts and starts, our submit raises, no DB row, the eventual result is keyed to a task_id we never recorded. Seedance video submits regularly take 20-45 s to return a task_id. |
| Poll interval | 4 s initial, ×1.5 backoff, 60 s cap | |
| Max poll time | 2700 s (45 min) | |
| Own-webhook POST timeout | 60 s | Our handler downloads provider media and uploads to S3 before responding. A 10 s budget read a *working* handler as a failure and re-fired every 60 s. |
| Own-webhook attempts | 3, then hand off to a reconciliation worker | Retrying delivery *inside the poll loop* re-POSTs the same terminal result every 60 s for the rest of the 45 min window. Self-inflicted webhook storm. |

Image input constraints **[CODE]** (`apimart_client.py:21-24`, derived from live apimart errors like *"Width must be between 300px and 6000px"*):

- Both axes must land in **[300, 6000] px**. We normalize (Lanczos up, then down) and re-upload before submit.
- Accepted formats: JPEG / PNG / WebP. We re-encode WebP to PNG as the safe portable choice.

### 1.3 Idempotency — the honest answer

**Neither gateway supports an idempotency key on submit.** Retry safety is entirely ours **[CODE]** (`studio_core/backends/studio/charging.py`):

- Table `core_idempotency_keys` with `UNIQUE (user_id, idempotency_key)`.
- Three branches: key absent -> always submit; key present + seen -> replay the stored `request_id`, do not submit or charge; key present + unseen -> submit, then atomically store `key -> request_id`. The unique constraint makes a concurrent same-key submit lose the insert race rather than record a second mapping.
- A second, independent guard sits at the webhook: a duplicate completion for a `request_id` is a no-op.

**Implication for your retries:** a network-level retry of a submit *will* create a second provider job and a second charge unless you dedupe before the HTTP call. Do not rely on the gateway.

### 1.4 Webhook auth (if you take the callback path)

**[CODE]** `utils/webhook_token.py`. Callback URL is `/webhook/<type>/<owner_id>/<token>`, where `token = HMAC-SHA256(WEBHOOK_CALLBACK_SECRET, ":".join(parts))` over the *owner identifier only* — not the body (provider-controlled) and not query params (routing hints). Verified with `hmac.compare_digest`. Fail-closed: unset secret means every callback 403s.

---

## 2. TTS

### 2.1 What exists, and what is proven

| Model id | Route | Cloning | Prod evidence |
|---|---|---|---|
| `elevenlabs-tts-multilingual-v2` | **kie** `elevenlabs/text-to-speech-multilingual-v2` | No | **Zero generations, ever.** Unproven. |
| `elevenlabs-tts-v3` | fal `fal-ai/elevenlabs/tts/eleven-v3` (kie lane deliberately removed — see §8.10) | No | 15 completed. **Hindi verified** 2026-09-01. |
| `elevenlabs-tts-turbo-v2.5` | kie lane removed 2026-08-20 | No | 2 attempts, both **failed**. |
| `cartesia-sonic` | **direct** `api.cartesia.ai` — no kie, no apimart | **Yes, instant clone** | 22 completed. **Hindi + Gujarati verified** 2026-09-02/03. |
| `minimax-voice-clone` | fal `fal-ai/minimax/voice-clone` | Yes | 0 completed. |

**Blunt read:** your stated preference (apimart + kie only) and your requirement (clone from a client-supplied reference) do not currently intersect. The only voice-cloning TTS we have ever gotten Hindi out of is Cartesia, on a direct API, with no gateway lane. See the questions at the end.

### 2.2 Hindi / Hinglish — what actually ran **[PROD]**

| Date | Model | Provider | Voice | Chars | Text | Result |
|---|---|---|---|---|---|---|
| 2026-09-01 | `elevenlabs-tts-v3` | fal | preset `River` | 671 / 720 / 746 | Devanagari Hindi, *contains Latin digits* — `"1000 साल पहले बना ये मंदिर… सूरज की किरणों को बिल्कुल सटीक तरीके से ट्रैक करता था"` | completed |
| 2026-09-02 | `cartesia-sonic` | direct | **clone** from `.wav` / `.mp3` | 1938 | Gujarati-script Hinglish | completed |
| 2026-09-03 | `cartesia-sonic` | direct | **clone** from `.wav` | 203 | Devanagari Hindi | completed |

Notes:
- `language_code` was **null on every one of these**. Devanagari worked without it.
- Cartesia's clone call hardcodes `language: "en"` in our live path **[CODE]** `audio_executor.py:604` — and still produced Hindi and Gujarati output. The clone language field evidently does not gate synthesis language.
- **[GAP]** No male-Hindi preset exists in our config. The 20 ElevenLabs voices we expose (`Aria, Roger, Sarah, Laura, Charlie, George, Callum, River, Liam, Charlotte, Alice, Matilda, Will, Jessica, Eric, Chris, Brian, Daniel, Lily, Bill`) are the English default library. Every verified Hindi run used `River` (a preset, gender-neutral-ish) or a clone.
- **[GAP]** "Completed" means the job returned audio and was charged. Nobody recorded whether the Hindi *pronunciation* was acceptable or how the digits were read. The DB cannot tell you that.

### 2.3 Cloning flow — Cartesia **[CODE]**

Two variants exist in-tree. **You want the second one.**

**(a) Ephemeral, used by the live `/audio` path** (`audio_executor.py:_generate_cartesia`): clone -> synthesize -> **DELETE the voice**, every single call. Good for privacy, wrong for you — you would pay clone latency on every render and could never A/B the client's two reference clips.

**(b) Persistent, used by `scripts/clone_brainrot_voices.py`:** clone once, keep the id, reference it forever. This is the shape your pipeline needs.

```
POST https://api.cartesia.ai/voices/clone
Headers:  Authorization: Bearer $CARTESIA_API_KEY
          Cartesia-Version: 2026-05-11        # see version note below
Body:     multipart/form-data
          clip     = <audio file bytes>       # field name is literally "clip"
          name     = "voice label"
          language = "en"                     # what the live path sends; Hindi still worked
          enhance  = "false"                  # optional
Returns:  { "id": "<voice_id>", ... }         # <- this is the addressable voice id
```

Synthesis (synchronous, returns raw bytes — no task id, no polling):

```
POST https://api.cartesia.ai/tts/bytes
Headers:  Authorization: Bearer $CARTESIA_API_KEY
          Cartesia-Version: 2026-05-11
          Content-Type: application/json
Body:
{
  "model_id": "sonic-3.5",
  "transcript": "<your script>",
  "voice": { "mode": "id", "id": "<voice_id>" },
  "output_format": { "container": "wav", "encoding": "pcm_f32le", "sample_rate": 44100 }
}
```

Deletion (only if you want ephemeral): `DELETE https://api.cartesia.ai/voices/{voice_id}`.

**Version header inconsistency [CODE]:** the audio executor sends `Cartesia-Version: 2026-05-11`; the WebSocket client and the clone script send `2026-03-01`. Both are live and both work. Pick one deliberately.

There is also a **WebSocket** path (`wss://api.cartesia.ai/tts/websocket?api_key=...&cartesia_version=...`) that returns **word-level timestamps** alongside PCM (`add_timestamps: true`, `context_id` for multiplexing). Speed is clamped 0.6–1.5. If you ever need to cut the script at word boundaries or drive captions, that is the only path in our stack that gives you timings.

### 2.4 Max input length

- **Our guard:** 10,000 characters, enforced for every per-1000-char-priced model **[CODE]** `audio_executor.py:698`. Over that returns `"Text is too long. Maximum 10,000 characters."`
- **[GAP]** Provider-side limits are not encoded anywhere in our config. Longest thing we have actually pushed through in prod is 1,938 chars (Cartesia) and 1,534 chars (ElevenLabs v3). Your ~80-word script is ~450–600 chars — comfortably inside everything we have tested.

### 2.5 Duration in the response — **no**

**[CODE]** Nothing in any TTS response carries a duration. Not ElevenLabs, not Cartesia, not MiniMax. The audio executor returns `{request_id, audio_key, content_type, file_size, pricing}` and that is all.

Duration must be **probed**. Our probe ladder **[CODE]** (`studio_core/backends/studio/media_duration.py`), which exists because a client-supplied duration is a billing input and therefore untrusted:

1. ffmpeg probe of the URL — **authoritative**
2. client-supplied hint, only if the probe failed *and* the hint is in `(0, max_seconds]`
3. `max_seconds` — fail-closed, in our favour

with an 8 s ceiling on the whole gather (each individual probe is separately bounded at 30 s), because this sits in the charge path of a web POST and a gateway timeout there becomes a **second charge**. Presigned S3 URLs get presigned first; every URL goes through an SSRF guard that resolves redirects hop-by-hop before the probe (a public URL that 30x-redirects to internal infra otherwise slips past a one-shot first-host check).

**This is the coupling between your step 2 and step 3.** The avatar model bills per output second and our cost path resolves duration in this order: `num_frames/fps -> explicit duration param -> default -> media_duration -> fallback_duration`. If nothing resolves, `fallback_duration` for `kling-avatar-v2` is **5 seconds** — you would charge for 5 s of a 35 s video.

### 2.6 Latin digits in Hindi text

**[CODE]** ElevenLabs exposes `apply_text_normalization` with allowed values `"auto" | "on" | "off"`, default `"auto"`, and it is wired end-to-end in our `parameter_mapping` for both v3 and multilingual-v2. **That is the built-in numeral-expansion control.** It is also never set in any prod row — every generation ran on `auto`.

So: **for ElevenLabs your `NUMERAL_WORDS` table is probably redundant** — set `apply_text_normalization: "on"` and test. The one Hindi row we have with a Latin digit (`"1000 साल"`) completed on `auto`, but **[GAP]** nobody listened to how it read the number.

Cartesia exposes no equivalent in our config or our client. **[GAP]** If you go Cartesia, assume you need your own expansion until proven otherwise.

### 2.7 Billing shape

`thousands = ceil(char_count / 1000)`, minimum 1 **[CODE]** `audio_executor.py:137`. A 550-character script bills as a full 1,000. Two 550-char scripts in one call bill as 1,100 -> 2,000. Batching is worth 2× here.

---

## 3. Video / avatar lipsync — the blocking question

### 3.1 Answer to spike 0.1: **30–40 s is proven. Do not rewrite the script.**

Every row below is a real prod job **[PROD]** (`behooked_studio`, `model in ('kling-avatar-v2','kling-ai-avatar')`), keyed on the resolved input-audio duration.

**`kling-avatar-v2` — completed:**

| via kie | 39 s · 30 s · 26 s · 25 s · 25 s · 24 s · 21 s · 20 s · 19 s · 16 s |
|---|---|
| **via fal** | 60 s · 60 s · 58 s · 50 s · 48 s · 46 s · 33 s · 21 s |

**`kling-ai-avatar` (v1) — completed:** 113 s · 95 s · 82 s · 82 s · 73 s · 71 s · **66 s (kie)** · 60 s · 53 s · 52 s · 50 s · 43 s · 38 s …

### 3.2 The real failure mode is **bytes, not seconds**

Every `"Audio size is too large"` failure we have ever recorded was a **`.wav`**:

| Duration | Format | Result | Same day / same user |
|---|---|---|---|
| 116 s | `.wav` | **failed** — "Audio size is too large" | — |
| 113 s | `.mp3` | **completed** | same user, 4 days earlier |
| 64 s | `.wav` | **failed** | — |
| 60 s | `.mp3` | **completed** | — |
| 37 s | `.wav` | **failed** | — |
| 53 s / 50 s / 48 s / 46 s / 39 s | `.wav` | **completed** | — |

A 37 s WAV failed while a 53 s WAV succeeded, so the threshold is a **byte count that depends on encoding** (sample rate / bit depth / channels), not a duration. Our own Cartesia output is `pcm_f32le` @ 44.1 kHz ≈ **176 KB/s** — a 40 s clip is ~7 MB. The same content as MP3 is ~640 KB.

> **Ship MP3 to the avatar model.** This single decision removes the entire failure class. **[GAP]** I cannot give you the exact byte threshold from these rows — nobody logged file sizes. If you want it pinned, that is a cheap binary-search spike.

### 3.3 Model + payload **[CODE]** (`models/config/lipsync_models.json`)

```
model id        kling-avatar-v2
variants        standard | pro
mode            ai-avatar
required        image_url, audio_url
optional        prompt          (our default is literally ".")
```

| Provider | Endpoint (standard) | Endpoint (pro) |
|---|---|---|
| **kie** (priority 1) | `kling/ai-avatar-standard` | `kling/ai-avatar-pro` |
| fal (fallback) | `fal-ai/kling-video/ai-avatar/v2/standard` | `.../pro` |

kie submit body:

```json
{
  "model": "kling/ai-avatar-pro",
  "callBackUrl": "https://.../webhook/lipsync_generation/<user_id>/<hmac>",
  "input": { "image_url": "https://<presigned>", "audio_url": "https://<presigned>", "prompt": "." }
}
```

`parameter_mapping` is identity for all three fields, and on a non-fal provider it acts as a **strict allowlist** — anything not in the map is dropped silently. Adding a field requires adding it to the mapping first.

**[GAP]** `kling-avatar-v2` declares **no `max_duration`** in our config. Other lipsync models in the same file do (8 s / 10 s / 15 s). Nobody wrote one for this model; the empirical ceiling above is what we have.

### 3.4 Async contract

Identical to §1.1 — kie `createTask` / `state` / `resultJson.resultUrls[0]`. Terminal on `success` or any of `{fail, FAILED, failed, error, ERROR}`. Intermediate states must be ignored, not treated as failures (our webhook returns `{"status":"processing"}` and drops them).

### 3.5 Latency **[PROD]**, kie lane

| Input audio | Wall clock |
|---|---|
| 16 s (standard) | ~256 s |
| 19–21 s (pro) | ~465–560 s |
| 24–26 s (pro) | ~560–680 s |
| 30 s (pro) | ~607 s |
| **39 s (pro)** | **~1191 s (~20 min)** |
| 60 s (pro, fal) | ~1250 s |

**Budget 8–20 minutes for a 30–40 s render.** Our poller's 45-minute ceiling is sized for exactly this. Do not build a UI that promises 2 minutes.

### 3.6 Result URL lifetime

**[GAP]** We have never measured kie's result-URL TTL, because we never depend on it.

**[CODE]** Every terminal webhook immediately downloads the provider URL and re-uploads to our own S3/CDN (`utils/media_ingestion.py:ingest_video_output` -> `download_url_to_new_bucket`). The raw provider URL is stored **only as a fallback when that download fails**, with an explicit inline comment that it *may expire*. That is the posture I would keep: treat the provider URL as valid for the duration of your webhook handler and nothing longer.

Note the mirror-image constraint on the **input** side: our presigned S3 URLs expire in **1 hour** (one path uses 6 h) **[CODE]** `utils/s3_utils.py:146,173`. A job that sits queued longer than that submits a dead URL.

---

## 4. Image edit / person replacement

### 4.1 Model

"GPT Max 2 Image" maps to **`gpt-image-2-max`** **[CODE]** (`models/config/image_models.json`) — **apimart-only**, endpoint `gpt-image-2`, `api_path: /images/generations`. Distinct from `gpt-image-2` (which has fal / apimart-official / kie lanes at very different prices — see §6).

### 4.2 How images are passed: **public URL. Not base64. Not upload-then-reference.**

**[CODE]** `image_executor._prepare_image_urls` converts private S3 URLs to **presigned HTTPS URLs**, then puts the list straight into `image_urls`. `ApimartClient.submit` does not touch `image_urls` beyond wrapping a scalar into a list.

Wire payload after mapping (`aspect_ratio -> size`, `num_images -> n`):

```json
{
  "model": "gpt-image-2",
  "prompt": "<see 4.4>",
  "image_urls": ["https://media.behooked.co/source/...", "https://..."],
  "size": "9:16",
  "resolution": "2K",
  "n": 1,
  "official_fallback": false
}
```

| Param | Allowed | Note |
|---|---|---|
| `size` (from `aspect_ratio`) | `1:1`, `4:3`, `3:4`, `16:9`, `9:16` | |
| `resolution` | `1K`, `2K`, `4K` | drives cost |
| `n` | 1–4 | |
| `quality` | **locked to `high`** | apimart's `gpt-image-2` endpoint does not accept a quality param. Our UI shows the pill disabled for parity. Sending it is pointless. |

Reference-image hygiene, both mandatory **[CODE]**:

1. **Both axes in [300, 6000] px.** We normalize with Lanczos before submit and re-upload to a sibling S3 key (never overwrite the original — Saved Kits and past job rows reference it).
2. **Serve from `/source/`, never `/transform/`.** The transform path does on-the-fly resize and **202s on a cold-cache miss**; apimart's reference fetcher has a tight timeout and bails. We rewrite `media.behooked.co/transform/{key}?...` -> `/source/{key}` before every submit. The same class of bug bit the element-sheet path on fal, where the fix was to presign against the source bucket so the downloader gets bytes immediately.

### 4.3 Prod reality **[PROD]**

`gpt-image-2-max` via apimart: **787 completed**, 21 deleted, 14 archived, **20 failed**. Avg latency **83 s**, max **644 s**. Last used 2026-09-07 — this is a hot path, not a museum piece.

Failure breakdown (all 20):

| Cause | n |
|---|---|
| Content-safety rejection (input prompt) | 5 |
| Content-safety filter (generated output) | 3 + 1 |
| `safety_violation: chatgpt upstream 400` | 2 |
| apimart timeout / `context deadline exceeded` | 2 + 1 |
| "requested option isn't supported by the provider" | 2 |
| `no_available_account: scheduler` | 1 |
| `rate_limited: upload reference 0: chatgpt upstream 429: create file failed` | 1 |
| `4k服务繁忙` (4K service busy) | 1 |
| our own credit check | 1 |

**11 of 20 failures are content safety.** A pipeline that swaps a real person into a branded plate will hit this. Budget a retry-with-softened-prompt path and a human-visible terminal failure. The `rate_limited: upload reference 0` line also confirms apimart *uploads your reference URL upstream* — reference count and size affect the failure surface.

### 4.4 Prompt structure that actually worked

Two real completed prod prompts on this exact model **[PROD]** (2026-09-05, Marathi Ganesh Utsav banner edits):

> `Edit this Marathi Ganesh Utsav banner. Reproduce it EXACTLY as-is — same layout, …`

> `Edit the supplied Marathi Ganeshotsav banner. Make EXACTLY ONE change and keep everything else identical …`

The pattern is **"reproduce exactly, change exactly one thing"** — negative-space framing, not descriptive framing. That is what is in the rows that succeeded.

The codified version we ship is `models/app_models/element_sheet/prompts/element_sheet.md` **[CODE]** — written for identity locking specifically. Its rules block, verbatim, is the best prior art we have for your identity-drift problem:

- *Reproduce the subject in the reference images exactly. Identity, facial features, colours, materials, finish, markings, logos, text and proportions are all fixed by the references — do not redesign, restyle, idealise, beautify or "improve" anything.*
- *Match the visual style of the references themselves. Do not impose a new art style.*
- *Every panel is the SAME subject … This sheet exists to lock identity for later generations, so drift between panels defeats its purpose.*
- *Where the references leave an angle unseen, infer it conservatively from what is visible. Never invent new features, text or branding.*

Reference-count discipline **[CODE]**: we cap at **4 references** (`MAX_REFS = 4`) — *"gpt-image-2/edit takes a handful of refs; more than this is noise and cost"* — and we **exclude previously generated sheets from the reference set**, because regenerating from a prior output compounds its own errors instead of going back to the source photos. Directly applicable to you: never feed a previous swap output back in as the identity reference.

### 4.5 Identity drift and geometry/scale drift

- **Identity drift:** addressed by the prompt discipline above and the 4-ref cap. That is all we have — no quantitative measurement.
- **Geometry / scale drift:** **[GAP], and this is a real one.** There is nothing in this codebase about holding a subject at a fixed pixel scale across an edit. Every image path we run is "generate a good-looking frame", never "preserve a pixel-locked composite region". Your fixed-position card overlay is a constraint nobody here has had to satisfy. I would not extrapolate from our experience — see question 4.

---

## 5. Lipsync repair

**Straight answer: we do not have one, and we do not have the scoring signal either.**

- **[GAP]** There is no repair model and no repair *mode* of `kling-avatar-v2`. Its only mode is `ai-avatar`.
- **[GAP]** There is no lip-sync quality score, confidence, or acceptance threshold anywhere in the backend. I searched for it. The only confidence values in the tree are MediaPipe face-detection scores in `dynamic_captions/face_tracker.py`, which drive **caption placement**, not sync quality. The threshold your pipeline is missing is missing from ours too.

The nearest usable tool is a **separate model**:

```
model id     sync-lipsync-v2                     (video-to-video, re-syncs an existing video to audio)
endpoint     fal-ai/sync-lipsync/v2  |  .../v2/pro
providers    fal only — no kie, no apimart lane
required     video_url, audio_url
optional     sync_mode ∈ { cut_off, loop, bounce, silence, remap }   default cut_off
pricing      standard $0.04/s · pro $0.06/s   (154 / 255 credits per second)
prod         40 completed, avg 717 s on the fal lane (max 1592 s)
```

`sync_mode` is how it resolves an audio/video duration mismatch — relevant if your repair pass runs against a Kling output that came back a beat short or long.

If you want a repair gate, the options are: (a) run `sync-lipsync-v2` unconditionally as a second pass — roughly doubles video cost and adds ~12 min; (b) a human review gate; (c) build the scorer. See question 6.

---

## 6. Cost per call

House conversion: **1 credit ≈ USD 1/3062 ≈ $0.000327** (`credits = ceil(provider_cost × 3062)`) **[CODE]**.

### 6.1 Image

| Model | Lane | Provider $ / image | Credits |
|---|---|---:|---:|
| **`gpt-image-2-max`** | **apimart `gpt-image-2`** | **1K $0.006 · 2K $0.012 · 4K $0.018** | 19 / 37 / 55 |
| `gpt-image-2` | apimart `gpt-image-2-official` | low+1K $0.006 · high $0.169 | 507 (t2i/i2i) |
| `gpt-image-2` | kie `gpt-image-2-image-to-image` | $0.02 (high) | 507 |
| `gpt-image-2` | fal `openai/gpt-image-2/edit` | $0.01 (low/1K) … **$3.52 (high/4K)** | — |
| `nano-banana-pro` | kie `nano-banana-pro` | 1K/2K $0.09 · 4K $0.12 | 121 |
| `nano-banana` | apimart `nano-banana-ext` | $0.0125 | 61 |
| `nano-banana` | kie `google/nano-banana` | $0.02 | 61 |

> `gpt-image-2-max` on apimart is **flat across quality** and 1–2 orders of magnitude cheaper than the fal `gpt-image-2/edit` lane. At 2K it is $0.012 against fal's $0.24 for the comparable medium/2K tier. That price gap is the single biggest reason to keep this on apimart.

### 6.2 TTS (per 1,000 chars, billed `ceil(chars/1000)`)

| Model | Lane | Provider $ / 1k | Credits |
|---|---|---:|---:|
| `elevenlabs-tts-multilingual-v2` | **kie** | **$0.06** | 181 |
| `elevenlabs-tts-multilingual-v2` | fal | $0.1996 | 181 |
| `elevenlabs-tts-v3` | fal | $0.1996 | 211 |
| `cartesia-sonic` | direct | $0.10 | 91 |
| `minimax-voice-clone` | fal | $0.0499 (01-turbo) … $0.1996 (02-hd) | 91–301 |

### 6.3 Avatar / lipsync (per output second)

| Model | Lane | Provider $/s | Credits/s | VIP credits/s |
|---|---|---:|---:|---:|
| **`kling-avatar-v2` standard** | **kie `kling/ai-avatar-standard`** | **$0.04** | 123 | 169 |
| **`kling-avatar-v2` pro** | **kie `kling/ai-avatar-pro`** | **$0.08** | 245 | 346 |
| `kling-avatar-v2` standard | fal | $0.0562 | 123 | 169 |
| `kling-avatar-v2` pro | fal | $0.115 | 245 | 346 |
| `sync-lipsync-v2` standard | fal | $0.04 | 154 | — |
| `sync-lipsync-v2` pro | fal | $0.06 | 255 | — |

Duration is **ceiled to the next whole second** before the rate is applied **[CODE]**.

### 6.4 Worked example — one 35 s talking head

| Step | Choice | Provider cost |
|---|---|---:|
| Image edit | `gpt-image-2-max` @ 2K, apimart | $0.012 |
| TTS | Cartesia clone, 550 chars -> 1k billed | $0.100 |
| Avatar | `kling-avatar-v2` **standard**, kie, 35 s | **$1.400** |
| **Total** | | **$1.512** |
| Same, `pro` | | **$2.912** |
| Same, `pro` + unconditional `sync-lipsync-v2` repair | | **$4.312** |

**The point for `vendor_limits.daily_credit_cap`:** the avatar call is **93–96 % of the spend** and it is **priced per second, not per call**. A call-count cap bounds volume and bounds spend by a factor that varies 3× depending on script length and 2× on variant. Cap on **seconds** or on **dollars**, and put the ceiling on the avatar step specifically — capping the image and TTS steps is rounding error.

**[GAP]** `vendor_limits`, `daily_credit_cap` and `NUMERAL_WORDS` do not appear anywhere in these six repos. They are your new pipeline's own config, so the table above is raw rates rather than a mapping into a schema I have not seen.

---

## 8. Gotchas — the ones we already paid for

**Gateway / transport**

1. **Apimart submit timeout must be 60 s, not 15 s.** Short timeouts produce *orphaned jobs*: apimart accepts and starts, our client raises, no DB row is created, and the eventual result is keyed to a task_id we have no record of. Silent money loss.
2. **Apimart has no webhooks — you own the poller.** Keep a **strong reference** to the polling task: `asyncio` holds only a weak ref to a bare `create_task()` result, so the poller gets garbage-collected mid-flight and the job hangs forever. Add a reconciliation worker (ours runs every 45 s) as a backstop for process restarts, and an in-process active-poll registry so the worker skips rows a live poller already owns.
3. **Never retry webhook delivery inside the poll loop.** A delivery failure on a terminal result re-POSTs the same result every 60 s for the remaining 45 minutes. Bounded attempts (3), then hand off.
4. **Order of operations in your own webhook handler:** parse -> create job row -> **idempotency return** -> do the expensive work. An idempotency guard placed *after* the download/upload, combined with a delivery timeout that the sender reads as a poll error, is a self-inflicted webhook storm.
5. **HTTP 200 + `code != 200` is an error on both gateways.** Check the body.
6. **kie `resultJson` is a JSON string.** Parse before indexing.
7. **Apimart video result is `result.videos[0].url[0]`** — `url` is a list.
8. **`/transform` CDN URLs 202 on cold-cache miss and provider fetchers bail.** Always hand providers a `/source/` path or a presigned URL that returns bytes on the first GET.
9. **Apimart rejects images outside [300, 6000] px on either axis.** Normalize before submit; write to a *sibling* key so concurrent jobs don't race the same PUT and the original stays intact for anything that references it.
10. **Type quirks are per-endpoint, not per-gateway.** kie wants `num_images` as a string, `image_input` present-but-empty for T2I, arrays for every `*_urls` field, and `duration` as int except on wan-2.5 where it must be a string.

**The expensive one — silent provider fallthrough**

11. **`elevenlabs-tts-v3` on kie: a 422 that cost 3× for months.** We added `similarity_boost` / `style` / `speed` to the kie whitelist because the *sibling* endpoints document them. kie's only Eleven-v3 route is `text-to-dialogue-v3`, whose schema has none of them (and whose `stability` is an **enum of `0 / 0.5 / 1.0`**, not a slider). Every submit posted three unknown fields -> **422** -> our code classified it as a retryable submission error -> **silently fell through to fal at $0.1996 vs $0.07** -> no trace in the DB, `_tried_providers` null on every row. The lane was dead for months while the config advertised it as priority 1.

    Two lessons, both structural: **(a)** validate against the *exact* endpoint's schema — sibling endpoints on the same gateway differ; **(b)** a fallback chain that swallows the reason is a cost leak you cannot see. Log which provider was tried and why it lost, or you will not find this.

**Media**

12. **Ship MP3 to the avatar model.** `"Audio size is too large"` is a byte limit, not a duration limit, and every one of our failures was a WAV. `pcm_f32le` @ 44.1 kHz is ~176 KB/s; a 40 s clip is ~7 MB against ~640 KB for the same content as MP3. Note our own Cartesia path *emits* WAV — transcode before the avatar step.
13. **TTS never returns duration.** ffmpeg probe is the only truth. Treat any client-supplied duration as a hint that loses to the probe, and fail **closed** (to the cap) rather than to zero — a broken probe path that returns 0 becomes free generations.
14. **Presigned input URLs expire in 1 h.** A job queued longer than that submits a dead URL to the provider.
15. **Copy the result to your own storage inside the webhook handler.** We have never measured kie's result-URL TTL because we never rely on it. Store the raw provider URL only as a fallback when your own download fails.
16. **Never feed a previous generation back in as an identity reference** — it compounds its own drift. Always re-reference the source photos. Cap references at ~4.

**Cost / correctness**

17. **Content safety is the dominant image failure** (11 of 20 on `gpt-image-2-max`). Real people in branded contexts trip it. Plan for it.
18. **Per-1000-char billing ceils.** 550 chars costs the same as 1,000. Batch.
19. **Our `provider` column reads `fal` for Cartesia jobs** even though they are direct API calls — mislabeled at write time. If you build reporting off a provider column, verify it against the code path, not the label.

---

## Open questions

Ordered by what they block. Q1–Q3 are one cluster and they gate step 2 entirely; Q4 gates the quality of step 1; the rest are scoping.

| Q | Blocks | Severity |
|---|---|---|
| 1 | Step 2 (voice) — the stated constraint set is empty | **Blocker** |
| 2 | Step 2 — decides whether Q1 is even a question | **Blocker** |
| 3 | Step 2 — voice selection | **Blocker** |
| 4 | Step 1 quality — no prior art exists here | High |
| 5 | Step 3 latency + risk shape | Medium |
| 6 | Step 4 — whether it exists at all | Medium |
| 7 | Cost-cap integration | Low (mapping only) |
| 8 | Optional precision on the WAV limit | Low |

1. **Is "apimart + kie only" a hard constraint for TTS?** It currently has no solution. The only voice-cloning TTS we have ever gotten Hindi out of is **Cartesia, on a direct API with no gateway lane**. The one ElevenLabs TTS on kie has **zero successful generations ever** and **no cloning support at all**. Options: (a) allow Cartesia direct for the TTS step only; (b) I go check kie's and apimart's live catalogues for a cloning TTS endpoint we have not configured; (c) drop cloning and use a preset. Which?

2. **What are the two client reference clips *for*?** Voice cloning, or choosing between presets? Our ElevenLabs config exposes no clone parameter whatsoever — only the 20 English presets. If it is cloning, the answer to Q1 is effectively forced.

3. **Does it have to be a Hindi *male* voice?** Every verified Hindi generation we have used the `River` preset or a clone. I have no evidence of a male-Hindi preset in any lane. A clone from the client's male reference sidesteps this entirely — another reason Q1 matters.

4. **What are the plate dimensions and the card's pixel rect?** This is the one place I have nothing. No path in this codebase preserves a pixel-locked composite region across an image edit — geometry/scale drift is genuinely unmeasured here. Give me the plate size and the card rect and I can at least tell you whether it survives the apimart [300, 6000] normalization and the `aspect_ratio -> size` mapping without a resample, which is a necessary-but-not-sufficient check.

5. **Is the 35 s a single continuous take?** 39 s is our longest kie success on `kling-avatar-v2` and it took ~20 minutes. Splitting into 2 shots halves the latency risk and gives you a natural cut point. Acceptable, or must it be one take?

6. **What should the repair gate be, given there is no scoring signal?** (a) always run `sync-lipsync-v2` as a second pass — +$1.40/video and +~12 min; (b) human review gate; (c) build a scorer (nothing to start from). I would not invent a threshold for you.

7. **Where does `vendor_limits` live?** It is in none of these six repos. If it is a new service I should be reading, point me at it and I will map §6 into its schema rather than handing you loose rates.

8. **Do you want the exact WAV byte threshold pinned?** It is a cheap binary-search spike against `kling-avatar-v2` and would let you keep WAV if you have a reason to. Otherwise the answer is just "use MP3".
