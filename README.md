# Castrol MAGNATEC — mechanic promo video pipeline

A batch video factory. For each mechanic submission pulled from the client's
export API it generates a vertical promo video — fixed Hindi script with the
mechanic's name, workshop and location spoken in a fixed voice, over one of six
pre-built garage scenes, with a personalisation card burned in — hosts it, and
POSTs the URL back to the client's delivery webhook.

WhatsApp is not in scope. The client runs that on Interakt; we see two HTTP
contracts and nothing else.

## Docs

| | |
|---|---|
| [`docs/TECH_DESIGN.md`](docs/TECH_DESIGN.md) | how it is built — modules, state machine, invariants |
| [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) | scope, client decisions, risks |
| [`CLAUDE.md`](CLAUDE.md) | working rules for this repo |

## Setup

```bash
uv sync
cp .env.example .env    # then fill it
```

Apply migrations in `supabase/migrations/` in order, against the project's own
Supabase instance.

> This project uses a **dedicated set of accounts** — GitHub, AWS, Supabase,
> Vercel and the AI provider are all separate from other BeHooked projects.
> See `CLAUDE.md`.

## Status

Phase 0. Foundation scaffolded, spikes not yet run.
