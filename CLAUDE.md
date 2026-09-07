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
uv sync                      # install
uv run castrol intake --from 2026-09-01 --to 2026-09-01
uv run castrol work --stage audio
uv run castrol poll
uv run castrol report
uv run pytest
uv run ruff check .
```

Migrations are forward-only numbered SQL in `supabase/migrations/`, applied in
order. There are no down migrations.

---

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

## Conventions

- Python 3.12+, `uv`, `src/` layout. Matches `behooked_studio_backend`.
- `structlog` JSON to stdout; `job_id` bound inside any job context.
- Stages implement the `Stage` protocol in `stages/base.py`. Stages do not
  write to `jobs` and do not decide retries — the orchestrator does both.
- `spikes/` is throwaway. It never imports `src/`, and `src/` never imports it.
- RLS is deny-all with no policies. Only the service role key is used, and only
  server-side.

---

## Current phase

**Phase 0.** Foundation scaffolded; spikes 0.1–0.6 not yet run.

The blocking unknown is spike 0.1: the finalised script is ~80 words (30–40s
spoken) against an originally assumed 18–25s. If the video model's max input
duration is below that, **the script changes, not the pipeline.** Little else is
worth building until that is answered.
