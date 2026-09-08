# Phase 0 spikes

Throwaway scripts. The only goal is to kill assumptions that would force a
rebuild later. They are allowed to be ugly. They are deleted when Phase 1 starts.

**Rules:** `src/` never imports spikes. The reverse is allowed and is now used
deliberately — [`prototype.py`](prototype.py) imports
[`stages/media.py`](../src/castrol_pipeline/stages/media.py) so the card and the
ffmpeg settings have one implementation rather than two that drift.

Real mechanic photos go in `spikes/in/` and outputs in `spikes/out/` — both are
gitignored, because this is personal data.

**Exit criteria for Phase 0:** one end-to-end video, hand-assembled, that a
human would accept. Do not start Phase 1 without it.

| # | Spike | Kills the assumption that | Status |
|---|---|---|---|
| 0.1 | Stage C on one plate + one full-length audio | the ~80-word script fits the video model's max input duration | **answered from prod evidence — not needed** |
| 0.2 | Same, measuring wall-clock generation time | concurrency 10 clears a day's batch | **latency answered; throughput still open** |
| 0.3 | Stage B person replacement on 3 real photos — sharp, soft, glasses | build/age/skin-tone transfer works with geometry locked | not run — **now the top spike** |
| 0.4 | Inspect stage C output for all 4 Castrol marks | brand marks survive two generative passes | not run |
| 0.5 | Fetch 5 Azure SAS URLs verbatim through the real HTTP client | nothing in the stack re-encodes the signature | script ready, needs URLs |
| 0.6 | TTS one real address + the `8 seconds` numeral | normalisation rules are sufficient | blocked on the TTS provider decision |

## What changed

`docs/TALKING_HEAD_PIPELINE_REFERENCE.md` answered 0.1 outright from prod job
rows: `kling-avatar-v2` has completed at 39s via kie and 60s via fal, so a
30–40s script needs no rewrite. It also gives 0.2's latency half — ~1191s for a
39s render — leaving only the throughput question of whether N concurrent jobs
clear a night's batch.

**0.3 is now the top spike**, and it is the one with no prior art: nothing in
the existing backend preserves a pixel-locked region across an image edit. The
fixed-position card depends entirely on that holding. Give it the plate
dimensions and the card rect and measure the drift directly.

0.6 is blocked until the TTS provider is chosen. Note that ElevenLabs has a
built-in `apply_text_normalization` control, so if that lane is picked the
numeral-expansion table may be redundant — test before building it.

0.5 is free and needs no vendor — run it as soon as real export rows exist.

## Files

| File | What it does |
|---|---|
| [`prototype.py`](prototype.py) | the full one-video path — image, audio, video, card, composite; resumable via `spikes/out/<run>/_state.json` |
| [`spike_05_sas_fetch.py`](spike_05_sas_fetch.py) | spike 0.5 — fetch Azure SAS URLs verbatim and check the bytes are an image |

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
