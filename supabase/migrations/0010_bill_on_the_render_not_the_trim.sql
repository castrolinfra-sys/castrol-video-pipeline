-- Say which duration the client is billed on, and round it to one decimal.
--
-- Two files now have two different lengths. `video_raw` is what kie returned;
-- `video_final` is that trimmed to its own audio, because kling-avatar-v2 has
-- no duration parameter and returns fixed-length blocks, leaving a silent tail
-- of up to ~2s after the speech ends. The composite cuts it.
--
-- We bill on the RENDER, not the trim: kie charges per output second and those
-- frames were generated and paid for. The trim is a presentation choice we make
-- afterwards at no cost, and it must not reduce what we recover.
--
-- The old expression already returned that number - but by accident. It asked
-- for max(duration_ms) across BOTH kinds and raw won only because it happens to
-- be the longer of the two. Nothing in the SQL said which one was meant, so the
-- day a longer asset kind appears, or raw is absent and only final remains, the
-- figure silently changes meaning with no migration and no review. Naming
-- `video_raw` costs nothing today and keeps that from happening.
--
-- Rounded to one decimal because that is the precision the number is quoted at.
-- 27.4670000000000000 is not more accurate than 27.5, it is just harder to
-- read, and a client-facing page should not display sixteen decimal places of
-- an ffprobe reading.
--
-- CREATE OR REPLACE with every existing column in its existing order: the only
-- shape Postgres accepts on a view that `daily_usage` is built on. Dropping
-- this one would take that with it.

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
  -- `video_raw` by name. The fallback stays: publish copies the asset row
  -- without re-probing, and `billed_seconds` is what the probe measured at
  -- submit time (invariant 12), so it is the same quantity from the other side.
  round(
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
    )::numeric, 1
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
   model to one decimal - what the vendor billed us and what the client is
   billed on - not the shorter trimmed file that ships. Deliberately carries NO
   cost, vendor, model or stage column - the panel must not show any of those,
   and the surest way to hold that line is for them not to be here.';

-- daily_usage sums job_usage.video_seconds, so it inherits the rounding. Its
-- total is re-rounded rather than left as a sum of rounded parts carrying a
-- trailing digit nobody asked for.
create or replace view daily_usage with (security_invoker = true) as
select
  (created_at at time zone 'Asia/Kolkata')::date          as day,
  count(*)                                                as jobs,
  count(*) filter (where status = 'completed')            as completed,
  count(*) filter (where status = 'failed')               as failed,
  round(
    coalesce(sum(video_seconds) filter (where status = 'completed'), 0)::numeric, 1
  )                                                       as seconds
from job_usage
group by 1;

comment on view daily_usage is
  'Per-day totals for the panel: jobs, completed, failed, and seconds of video
   delivered. Days with no activity are absent rather than zero - the panel
   fills gaps, because a view cannot know how far back the caller wants to look.';
