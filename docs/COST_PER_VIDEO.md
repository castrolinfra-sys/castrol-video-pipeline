# Cost per video — Castrol MAGNATEC pipeline

**Rates:** video $0.036/s (standard) · $0.072/s (Pro) · image $0.014 flat · TTS $0.00005/char
**Assumptions:** ₹100 = $1 · 17.4 chars/sec of speech · kie ceils to whole seconds

## Standard — `kling/ai-avatar-standard`, 720x1280

| Duration | Image | TTS | Video | Total $ | Total ₹ | $/sec | ₹/sec |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1s | 0.0140 | 0.0008 | 0.0360 | **0.0508** | **₹5.08** | 0.0508 | 5.08 |
| 5s | 0.0140 | 0.0044 | 0.1800 | **0.1984** | **₹19.84** | 0.0397 | 3.97 |
| 20s | 0.0140 | 0.0174 | 0.7200 | **0.7514** | **₹75.14** | 0.0376 | 3.76 |
| 25s | 0.0140 | 0.0218 | 0.9000 | **0.9358** | **₹93.58** | 0.0374 | 3.74 |

## Pro — `kling/ai-avatar-pro`, 1072x1920

| Duration | Image | TTS | Video | Total $ | Total ₹ | $/sec | ₹/sec |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1s | 0.0140 | 0.0008 | 0.0720 | **0.0868** | **₹8.68** | 0.0868 | 8.68 |
| 5s | 0.0140 | 0.0044 | 0.3600 | **0.3784** | **₹37.84** | 0.0757 | 7.57 |
| 20s | 0.0140 | 0.0174 | 1.4400 | **1.4714** | **₹147.14** | 0.0736 | 7.36 |
| 25s | 0.0140 | 0.0218 | 1.8000 | **1.8358** | **₹183.58** | 0.0734 | 7.34 |

## Summary

| | Standard | Pro |
|---|---:|---:|
| Marginal second | ₹3.60 | ₹7.20 |
| One 25s video | ₹93.58 | ₹183.58 |
| 500 videos @ 25s | ₹46,788 | ₹91,788 |

**Pro premium at 25s: ₹90 per video, ₹45,000 per 500.**

## Notes

- Only the $0.014 image cost is fixed, so per-second cost flattens by ~5s — there
  is no volume discount in making videos longer. Runtime is the only lever.
- TTS is ~2% of the total at 25s standard, ~1% on Pro.
- Composite, card render and publish are local ffmpeg/Pillow — free.
- The avatar step bills per OUTPUT second and kie ceils to whole seconds, so
  24.8s bills as 25s. Negligible at our ~25s scripts; a 100% overcharge on a 1s clip.
- Pro is the resolution switch. `kling/ai-avatar-standard` returns 720x1280 whatever
  it is fed; `kling/ai-avatar-pro` returns 1072x1920. There is no resolution
  parameter on either endpoint — `VIDEO_MODEL_ID` is the whole control, and
  `Settings.video_is_pro` derives the billing rate from it.
- Measured render times on the same 27s input: standard ~7 min, Pro ~11.5 min.

## What the client is billed on

The **render** length, not the trimmed file. `kling-avatar-v2` takes no duration
parameter and returns fixed-length blocks, so it hands back up to ~2s of silence
after the speech ends; the composite cuts that off. kie charges per output
second and those frames were generated either way, so the trim is a free
presentation choice made afterwards and must not reduce what we recover.

`job_usage.video_seconds` names `kind = 'video_raw'` explicitly for that reason
(migration `0010`) and rounds to one decimal. It used to take `max()` across raw
and final, which got the same answer only because raw happens to be longer.

## kie credits

**~207 credits per USD**, measured 2026-09-14: nine standard renders totalling
250 billed output seconds consumed exactly 1864 credits, i.e. 7.456 credits per
second, which at $0.036/s gives 207. `CLAUDE.md` used to say 166, read off a
refusal message — wrong by a quarter, and the kind of number worth re-measuring
after any batch.

## Measured, not modelled

The table above is the model. This is what 11 real renders across 9 jobs
actually billed, read out of `stage_runs` on 2026-09-15:

| stage | runs | avg | min | max |
|---|---|---|---|---|
| `video` | 11 | **$1.0567** | $0.9720 | $1.1200 |
| `audio` | 11 | $0.0226 | $0.0216 | $0.0236 |
| `image` | 11 | $0.0140 | — | — |

Billed render length 27–31s, **average 29.4s** — so **$1.093 all-in per video**.
Use $1.10 for planning and $1.15 if you want a margin. Retry pressure is real
but small: only `video` has ever retried, one row of eleven reaching three
attempts.

## The discrepancy is closed

This section used to record that `common/budget.py` pinned `$0.04/$0.08` and
over-reserved by 11.1%. `cf00e4a` corrected the constants to `$0.036/$0.072`,
so reservations now match the tables above and `job_costs` reconciles against
the vendor invoice. Re-measure after any batch regardless; a rate that drifted
once can drift again.

`docs/TALKING_HEAD_PIPELINE_REFERENCE.md` still shows $0.04/s and that is
correct there — it records what the other BeHooked stack measured, and is not
a source of truth for this pipeline's config.
