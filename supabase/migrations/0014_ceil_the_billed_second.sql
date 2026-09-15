-- Ceil the billed second, per render, in the view.
--
-- `0010` rounded `video_seconds` to one decimal. That was the instruction then;
-- it is not the instruction now. kie charges per output second and rounds UP —
-- a 24.2s render is billed as 25s — so the client is billed on the CEILING of
-- the raw render, and the panel must show that number and no other.
--
-- Two separate ways the old shape under-recovered, both fixed here, and both
-- fixable ONLY at this level rather than in the panel's formatting:
--
-- 1. ROUNDING CROSSED THE BOUNDARY DOWNWARD. `round(27.04, 1)` is `27.0`, which
--    ceils to 27 where kie billed 28. The panel cannot recover a second that
--    SQL has already discarded, so ceiling had to move in here. It bites
--    whenever the true fraction lands in .01-.04 — invisible in today's rows
--    only because .5 happens to round to itself.
--
-- 2. TOTALS CEILED THE SUM INSTEAD OF SUMMING THE CEILINGS. Nine renders on
--    2026-09-14: the per-row figures add to 250s, while ceiling their 245.1s
--    total gives 246s. kie issued nine charges, each rounded up on its own, so
--    250 is the invoice and 246 was a number matching nothing. Ceiling per row
--    HERE makes `daily_usage` a sum of already-billed integers, so every total
--    on the usage page reconciles with the column printed above it.
--
-- `panel/lib/format.ts` still calls Math.ceil. That is now a no-op on anything
-- this view returns, and it stays: it is the guard for the day someone changes
-- this expression back.
--
-- Type is unchanged — ceil(numeric) is numeric, as round(numeric, int) was — so
-- CREATE OR REPLACE accepts it. Every column is restated in its existing order
-- for the same reason as 0010: this is the only shape Postgres will take on a
-- view that `daily_usage` is built on, and DROP would take that one with it.
--
-- Nothing had been quoted to the client at the time of writing (confirmed
-- 2026-09-15), so no already-issued figure moves under anyone. The displayed
-- length of EXISTING jobs does change — 27.5 becomes 28 — because this is a
-- view over the same stored `duration_ms`, not a backfill. No data is written.

create or replace view job_usage with (security_invoker = true) as
select
  j.id                  as job_id,
  j.status,
  j.created_at,
  j.failure_reason,
  s.mechanic_id,
  s.phone_e164          as whatsapp_number,
  s.card_phone_e164,
  s.user_name,
  s.workshop_name,
  s.address_raw,
  -- `video_raw` by name, from 0010, and that is the load-bearing part: this is
  -- the RENDER we paid for, never the shorter trimmed file that ships. The
  -- fallback stays — publish copies the asset row without re-probing, and
  -- `billed_seconds` is what the probe measured at submit time (invariant 12),
  -- so it is the same quantity arrived at from the other side.
  ceil(
    coalesce(
      (select max(a.duration_ms) / 1000.0
         from assets a
        where a.job_id = j.id
          and a.kind = 'video_raw'
          and a.duration_ms is not null),
      (select max(sr.billed_seconds)
         from stage_runs sr
        where sr.job_id = j.id
          and sr.stage = 'video'
          and sr.status = 'succeeded')
    )::numeric
  )                 as video_seconds,
  coalesce(
    (select d.cdn_url from deliveries d where d.job_id = j.id),
    (select a.cdn_url
       from assets a
      where a.job_id = j.id
        and a.kind = 'video_final'
        and a.cdn_url is not null
      order by a.created_at desc
      limit 1)
  )                 as video_url
from jobs j
join submissions s on s.id = j.submission_id;

comment on view job_usage is
  'One row per job, shaped for the client-facing admin panel: who it was for,
   what state it is in, how many seconds of video it produced, and where that
   video can be watched. `video_seconds` is the RENDER length from the avatar
   model, CEILED to a whole second - what the vendor billed us and what the
   client is billed on - not the shorter trimmed file that ships. Never round it
   to nearest: rounding below what kie charged is the one error direction that
   loses money silently. Deliberately carries NO cost, vendor, model or stage
   column - the panel must not show any of those, and the surest way to hold
   that line is for them not to be here.';

-- The re-round from 0010 is GONE, deliberately. `video_seconds` is now a whole
-- number per job, so this is a sum of integers and already exact; rounding it
-- again would be a second rounding applied to a quantity that has none left,
-- and it is what was collapsing nine separate charges into one.
create or replace view daily_usage with (security_invoker = true) as
select
  (created_at at time zone 'Asia/Kolkata')::date          as day,
  count(*)                                                as jobs,
  count(*) filter (where status = 'completed')            as completed,
  count(*) filter (where status = 'failed')               as failed,
  coalesce(sum(video_seconds) filter (where status = 'completed'), 0)::numeric
                                                          as seconds
from job_usage
group by 1;

comment on view daily_usage is
  'Per-day totals for the panel: jobs, completed, failed, and seconds of video
   delivered. `seconds` is a sum of per-render CEILED seconds, so it equals the
   sum of the figures the panel prints per job and matches what the vendor
   charged - not the ceiling of a raw total, which is a smaller number matching
   no invoice. Days with no activity are absent rather than zero - the panel
   fills gaps, because a view cannot know how far back the caller wants to look.';
