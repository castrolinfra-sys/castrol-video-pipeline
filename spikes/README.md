# Phase 0 spikes

Throwaway scripts. The only goal was to kill assumptions that would force a
rebuild later, and they are allowed to be ugly.

**Phase 0 is over and `prototype.py` is staying.** It is no longer a spike: it
is the cheapest way to look at a render before a prompt, model id or card change
reaches the worker — which matters more now that the worker tracks `:latest`, so
a merge is a deploy with no visual gate. It also holds the ONE card
implementation by importing `stages/media.py` rather than copying it.

**Rules:** `src/` never imports spikes. The reverse is allowed and is now used
deliberately — [`prototype.py`](prototype.py) imports
[`stages/media.py`](../src/castrol_pipeline/stages/media.py) so the card and the
ffmpeg settings have one implementation rather than two that drift.

Real mechanic photos go in `spikes/in/` and outputs in `spikes/out/` — both are
gitignored, because this is personal data.

**Phase 0's exit criterion was met**: an end-to-end video, hand-assembled, that
the client reviewed — three times, on the lower-third alone.

| # | Spike | Kills the assumption that | Status |
|---|---|---|---|
| 0.1 | Stage C on one plate + one full-length audio | the ~80-word script fits the video model's max input duration | **answered from prod evidence — never needed to run** |
| 0.2 | Same, measuring wall-clock generation time | concurrency 10 clears a day's batch | **latency answered** (~8–20 min, measured over 11 renders); throughput still open |
| 0.3 | Stage B person replacement on 3 real photos — sharp, soft, glasses | build/age/skin-tone transfer works with geometry locked | **still the top open question** — never run as a controlled spike, though stage B has now run for real on 11 jobs |
| 0.4 | Inspect stage C output for the Castrol marks | brand marks survive two generative passes | **answered in production** — 9/9 renders on 2026-09-14 read a clean chest `Castrol`. Note there are two marks now, not four: the 2026-09-11 artwork dropped the cap and the sleeve logo |
| 0.5 | Fetch 5 Azure SAS URLs verbatim through the real HTTP client | nothing in the stack re-encodes the signature | **answered in production** — intake has fetched real SAS urls since 2026-09-08 and landed them in S3 |
| 0.6 | TTS one real address + the `8 seconds` numeral | normalisation rules are sufficient | **answered** — Cartesia chosen, rules built and unit-tested (`tests/test_prep.py`) |

## What changed

`docs/TALKING_HEAD_PIPELINE_REFERENCE.md` answered 0.1 outright from prod job
rows: `kling-avatar-v2` has completed at 39s via kie and 60s via fal, so a
30–40s script needs no rewrite. It also gave 0.2's latency half, since confirmed
on our own renders.

**0.3 is the one that is genuinely still open**, and it is the one with no prior
art: nothing in the existing backend preserves a pixel-locked region across an
image edit, and the fixed-position card depends entirely on that holding. What
it needed is now known — plates are 1152x2048, the card rect is
`y 72.27%..87.00%`, and the hands measure 60–70% of frame height — but the drift
itself has never been measured per job, and no check looks for it.

0.6 is closed by the provider decision: **Cartesia, direct API**, with the voice
created by hand in the dashboard. The ElevenLabs note that used to sit here —
that its built-in `apply_text_normalization` might make the numeral table
redundant — does not apply to Cartesia, and the table is built and tested.

## Files

| File | What it does |
|---|---|
| [`prototype.py`](prototype.py) | the full one-video path — image, audio, video, card, composite; resumable via `spikes/out/<run>/_state.json` |
| [`spike_05_sas_fetch.py`](spike_05_sas_fetch.py) | spike 0.5 — fetch Azure SAS URLs verbatim and check the bytes are an image. Kept for diagnosing a single unreachable blob, which `cycle.orphan_photo_failed` now surfaces by job |

The pipeline equivalent of `prototype.py` is
[`stages/real.py`](../src/castrol_pipeline/stages/real.py) driven by
[`orchestrator.py`](../src/castrol_pipeline/orchestrator.py); use
`castrol seed-job` + `castrol drain` for anything that needs a database row.

## 0.5 — SAS URL fetch

```bash
# one URL per line, exactly as they appear in the export. Do not edit them.
uv run python spikes/spike_05_sas_fetch.py spikes/in/sas_urls.txt
```

Verifies that the URL survives the HTTP client untouched, and that what comes
back is actually an image — a permissions failure returns 200 with an HTML body.
