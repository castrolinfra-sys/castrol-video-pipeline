# Phase 0 spikes

Throwaway scripts. The only goal is to kill assumptions that would force a
rebuild later. They are allowed to be ugly. They are deleted when Phase 1 starts.

**Rules:** spikes never import `src/`, and `src/` never imports spikes. Real
mechanic photos go in `spikes/in/` and outputs in `spikes/out/` — both are
gitignored, because this is personal data.

**Exit criteria for Phase 0:** one end-to-end video, hand-assembled, that a
human would accept. Do not start Phase 1 without it.

| # | Spike | Kills the assumption that | Status |
|---|---|---|---|
| 0.1 | Stage C on one plate + one full-length audio | the ~80-word script fits the video model's max input duration | not run |
| 0.2 | Same, measuring wall-clock generation time | concurrency 10 clears a day's batch | not run |
| 0.3 | Stage B person replacement on 3 real photos — sharp, soft, glasses | build/age/skin-tone transfer works with geometry locked | not run |
| 0.4 | Inspect stage C output for all 4 Castrol marks | brand marks survive two generative passes | not run |
| 0.5 | Fetch 5 Azure SAS URLs verbatim through the real HTTP client | nothing in the stack re-encodes the signature | script ready, needs URLs |
| 0.6 | TTS one real address + the `8 seconds` numeral | normalisation rules are sufficient | not run |

## Order

**0.1 first, and possibly alone.** If the video model caps input duration below
30–40s, the script changes and several other spikes are re-run against a
different script. Everything downstream of it is wasted work until it is
answered.

0.5 is free and needs no vendor — run it as soon as real export rows exist.

## 0.5 — SAS URL fetch

```bash
# one URL per line, exactly as they appear in the export. Do not edit them.
uv run python spikes/spike_05_sas_fetch.py spikes/in/sas_urls.txt
```

Verifies that the URL survives the HTTP client untouched, and that what comes
back is actually an image — a permissions failure returns 200 with an HTML body.
