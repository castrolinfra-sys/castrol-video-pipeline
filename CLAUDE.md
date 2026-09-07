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

```bash
uv run castrol intake --from 2026-09-01 --to 2026-09-01
```

```bash
uv run castrol work --stage audio
```

```bash
uv run castrol poll
```

```bash
uv run castrol report
```

### Checks

```bash
uv run pytest
```

```bash
uv run ruff check .
```

### Balances — check before any run that spends

```bash
curl -sS https://api.apimart.ai/v1/user/balance -H "Authorization: Bearer $APIMART_API_KEY"
```

```bash
curl -sS https://api.kie.ai/api/v1/chat/credit -H "Authorization: Bearer $KIE_API_KEY"
```

apimart reports USD directly (1 apimart credit = $0.10). kie reports its own
credits at roughly 166 per USD — inferred from a refusal, not confirmed.

### Client export

```bash
curl -sS "https://capi.letschbang.com/api/submissions/export/vendor?from=2026-09-02&to=2026-09-03" -H "apikey: $CLIENT_EXPORT_API_KEY"
```

Returns **CSV**, not JSON. Rate limit 100 / 900s.

### Migrations

Forward-only numbered SQL in `supabase/migrations/`, applied in order. There
are no down migrations. Apply through the Supabase MCP or the SQL editor.

### AWS

One-time admin setup only — see `infra/README.md`. The pipeline's IAM user
(`castrol-local`) can read and write objects but cannot delete them or change
bucket configuration, which is deliberate: retention belongs to lifecycle
rules, not application code.

---

## Inputs per video

| Input | Source | Used for |
|---|---|---|
| Plate (uniform + background) | frozen, chosen by export `outfit` + `background` | the scene |
| Mechanic photo | export `image_url` | face/build swapped onto the plate |
| Name | export `user_name` | **spoken** + card |
| Workshop name | export `workshop_name` | **spoken** + card |
| Location | export `address` | **spoken** + card |
| Phone | export `whatsapp_number` | **card only**, never spoken |

Only name, workshop and location vary inside the script. `whatsapp_number` is
the delivery key and the card number; `mechanic_phone_number` is unreliable and
is not used.

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
Pass `image_url_raw` to the HTTP client verbatim. Never `quote`/`unquote` it,
never form-decode it, never rebuild it from parsed components, never route it
through a URL-normalising client. Azure signs over exact bytes; any
normalisation returns 403 that reads like a permissions failure. Encoding is
inconsistent *within a single URL*, so it looks wrong — it is not.

**2. Validate magic bytes, not status codes.**
A permissions failure can return HTTP 200 with an HTML body. Without a
magic-byte check, that writes login pages into S3 as `.jpg`.

**3. Every paid vendor call goes through `common/budget.py`.**
It calls the `reserve_vendor_call()` Postgres function and refuses on `false`.
No stage may reach a vendor by any other path. This is the only thing between a
retry bug and a real bill.

**4. `input_hash` covers model ids and prompt/template versions.**
That is what makes changing a model id regenerate instead of skip. Canonical
JSON lives in `common/hashing.py` and nothing else may serialise for hashing.

**5. No generative model ever renders text.**
The personalisation card is a deterministic Pillow render burned in with ffmpeg
after video generation. Keep it that way.

**6. Geometry is preserved in stage B.**
The card sits at a fixed pixel position. If person replacement shifts subject
scale or the belt line, the card lands on the mechanic's hands. Change /
Preserve / Constrain prompt structure is deliberate.

**7. Async stages submit and release.**
Stage C writes `vendor_task_id` and returns. The poller reconciles. Never block
a worker on a vendor poll.

**8. Rejected rows are not repaired.**
Intake rejects with a stable code. A repaired row is a row whose output nobody
can explain.

**9. Timestamp format is pinned, never inferred.**
`EXPORT_TIMESTAMP_FORMAT` in env. The raw string is also stored so a wrong
format can be reparsed without re-pulling.

**10. Real mechanic photos never enter git.**
`spikes/in/`, `spikes/out/` and media extensions are gitignored. This is
personal data — face photos joinable to phone numbers.

---

The rest come from [`docs/TALKING_HEAD_PIPELINE_REFERENCE.md`](docs/TALKING_HEAD_PIPELINE_REFERENCE.md),
which is prod-measured evidence from the existing BeHooked backend. Each one
below is a failure someone already paid for.

**11. Ship MP3 to the avatar model, never WAV.**
`"Audio size is too large"` is a byte limit, not a duration limit. Every
observed failure was a WAV — a 37s WAV failed while a 53s WAV succeeded.
`pcm_f32le` @44.1kHz is ~176 KB/s, so 40s is ~7 MB against ~640 KB as MP3.
Stage A transcodes before handing off.

**12. Probe audio duration with ffmpeg. Never trust a supplied duration.**
No TTS provider returns duration. The avatar model bills *per output second*,
so the probe sits in the charge path. Fail **closed** to the cap, never to
zero — and note `kling-avatar-v2`'s `fallback_duration` is **5 seconds**, so a
missed probe bills 5s for a 35s video and no cap notices.

**13. Neither gateway supports an idempotency key on submit.**
Not kie, not apimart — the field does not exist. A network-level retry of a
submit creates a second provider job and a second charge. Dedupe *before* the
HTTP call; never blind-retry a submit that may have landed. Reconcile instead.

**14. Check the body `code`, not the HTTP status.**
Both gateways return HTTP 200 with `code != 200` on error. Also: kie's
`resultJson` is a JSON *string* — parse before indexing. apimart's video
result is `result.videos[0].url[0]` — `url` is a list.

**15. Copy provider result URLs to our storage immediately.**
Treat a provider URL as valid for the duration of the handler and no longer.
Mirror constraint on the input side: presigned URLs expire in 1 hour, so a job
that sits queued longer submits a dead URL. Presign at submit time, not at
enqueue time.

**16. Hand providers a URL that returns bytes on the first GET.**
Public or presigned, from a source path — never a CDN transform path, which
202s on a cold-cache miss and the provider's fetcher bails. Images must be
within [300, 6000] px on **both** axes; normalise to a *sibling* key, never
overwrite the original.

**17. Never feed a generated image back in as an identity reference.**
It compounds its own drift. Always re-reference the source photo. Cap
references at ~4.

**18. Content safety is the dominant image failure** — 11 of 20 observed on
this exact model. Swapping a real person into a branded plate is precisely the
trigger. Needs a softened-prompt retry path and a visible terminal state.

**19. Log which provider was tried and why it lost.**
A fallback chain that swallows the reason is a cost leak nobody can see: a
dead kie lane 422'd for *months*, was classified retryable, silently fell
through to a lane costing 3×, and left no trace in the database. Validate
against the exact endpoint's schema — sibling endpoints on the same gateway
accept different fields.

---

## Conventions

- Python 3.12+, `uv`, `src/` layout. Matches `behooked_studio_backend`.
- `structlog` JSON to stdout; `job_id` bound inside any job context.
- Stages implement the `Stage` protocol in `stages/base.py`. Stages do not
  write to `jobs` and do not decide retries — the orchestrator does both.
- `spikes/` is throwaway. It never imports `src/`, and `src/` never imports it.
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

**Phase 1.1–1.4 landed** (config, logging, budget, intake, prep, orchestrator
with stub stages). Schema and budget applied to Supabase.

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

**The open unknown is geometry drift** — spike 0.3. See below.
