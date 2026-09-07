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
  [B] image    apimart gpt-image-2 @2K      person replacement on the plate
        │
  [A] audio    Cartesia sonic-3.6           script -> mp3 + probed duration
        │
  [C] video    kie kling-avatar-standard    avatar lipsync   (8-20 MINUTES)
        │
  [D] card     Pillow + ffmpeg              burn in the lower-third
        │
   final mp4 -> S3 -> CDN -> POST client webhook
```

A and B are independent and run ahead. C is the bottleneck and 96% of the cost.

---

## Setup

```bash
uv sync
```

```bash
cp .env.example .env
```

Fill `.env` — every variable is documented there. Then apply the migrations in
`supabase/migrations/` in order, against this project's own Supabase instance.

> This project uses a **dedicated set of accounts** — GitHub, AWS, Supabase,
> Vercel, apimart, kie and Cartesia are all separate from other BeHooked
> projects. See [`CLAUDE.md`](CLAUDE.md).

---

## Commands

### Prototype — one video, end to end

The Phase 0 path. No database, no orchestrator; it does by hand what the
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

---

## Docs

| | |
|---|---|
| [`docs/TECH_DESIGN.md`](docs/TECH_DESIGN.md) | how it is built — modules, state machine, invariants |
| [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) | scope, client decisions, risks |
| [`docs/TALKING_HEAD_PIPELINE_REFERENCE.md`](docs/TALKING_HEAD_PIPELINE_REFERENCE.md) | prod-measured evidence from the existing BeHooked backend |
| [`docs/CARTESIA_API_DOCS.md`](docs/CARTESIA_API_DOCS.md) | Cartesia reference |
| [`infra/README.md`](infra/README.md) | AWS setup commands |
| [`CLAUDE.md`](CLAUDE.md) | working rules and invariants |

---

## Status

Phase 0 — prototype. Audio, image, card and composite all verified against
real providers. Plates are being authored.
