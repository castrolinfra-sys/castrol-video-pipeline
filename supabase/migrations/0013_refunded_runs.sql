-- Stop counting money the vendor gave back.
--
-- EVERY failed vendor job refunds its credits. Confirmed against the provider
-- dashboard on 2026-09-15, and there is no exception - not for a rejection, not
-- for a task that timed out on our side and looks completed from theirs. A
-- stage_run that ends `failed` cost nothing, whatever it reserved at submit.
--
-- `job_costs` summed every attempt, so a job that failed twice before it
-- succeeded read roughly three times its real bill. That view is what a client
-- quote is built from and what gets reconciled against the invoice, so being
-- wrong in the expensive-looking direction is not the safe kind of wrong.
--
-- Cost still lands at SUBMIT and that does not change (invariant 24). The
-- record that a submit HAPPENED is what reconciliation needs; what was missing
-- was a way to say the money came back. But the REASON written beside that
-- invariant - "a task that never completes still cost money" - is wrong for
-- this vendor and must not be restated.
--
-- This also settles something that looked like a bug and is not. On a retry
-- WITHIN one row, attempt 2's `mark_running` overwrites attempt 1's cost_usd,
-- which read as losing a charge. With refunds it is exactly right: attempt 1
-- was refunded, so the last attempt's figure is the only one ever billed.
--
-- `vendor_usage` is deliberately NOT adjusted. Its reservation is what bounds a
-- runaway loop, and a budget that refunds on failure is one a retry loop can
-- walk straight through (common/budget.py). So the daily caps still count
-- failed attempts - the safe direction, and 0011 already made the CALL cap the
-- real ceiling rather than the cost cap.

alter table stage_runs
  add column refunded boolean not null default false;

comment on column stage_runs.refunded is
  'The vendor refunded this attempt. Set automatically on any TERMINAL failure
   that carried a cost - every failed job refunds, with no exception. Never set
   on a succeeded run, which is why `paid_calls` needs no filter for it.
   `job_costs` excludes these from cost_usd and reports them as refunded_usd,
   so the view shows what was actually billed.';

-- Backfill. A RETRYABLE failure returns the row to `pending`, so a row sitting
-- at `failed` is terminal by construction and every one of these was refunded.
update stage_runs
   set refunded = true
 where status = 'failed'
   and cost_usd is not null
   and cost_usd > 0;

-- The only question asked of this column is "what came back", and refunded runs
-- are the minority, so the index is partial.
create index stage_runs_refunded_idx on stage_runs (job_id) where refunded;

-- CREATE OR REPLACE with every existing column in its existing order and type,
-- the new one appended. cost_usd changes MEANING, not shape.
--
-- video_seconds is left exactly as it was on purpose: it is a duration, not a
-- charge, and narrowing it here would be a second change hiding inside this one.
create or replace view job_costs with (security_invoker = on) as
  select j.id                                              as job_id,
         j.status,
         coalesce(sum(sr.cost_usd) filter (where not sr.refunded), 0)
                                                           as cost_usd,
         coalesce(sum(sr.cost_usd) filter (where sr.stage = 'video'
                                             and not sr.refunded), 0)
                                                           as video_cost_usd,
         max(sr.billed_seconds) filter (where sr.stage = 'video')
                                                           as video_seconds,
         count(*) filter (where sr.status = 'succeeded' and sr.cost_usd > 0)
                                                           as paid_calls,
         count(*) filter (where sr.status = 'failed')      as failed_runs,
         j.created_at,
         j.completed_at,
         coalesce(sum(sr.cost_usd) filter (where sr.refunded), 0)
                                                           as refunded_usd
    from jobs j
    left join stage_runs sr on sr.job_id = j.id
   group by j.id;

comment on view job_costs is
  'Per-video spend, counting only what was actually billed. Sums every ATTEMPT
   that was NOT refunded, so a job that retried the video step shows the real
   cost rather than one attempt''s - and a job that failed and was refunded
   shows zero rather than a charge that never landed. refunded_usd is what came
   back, kept visible so the difference is auditable instead of invisible.';
